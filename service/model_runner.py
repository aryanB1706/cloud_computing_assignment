"""Model download, loading and inference for the command parser (Task 3).

The model and tokenizer are downloaded from object storage at start-up.  The
container image contains no weights.  ``MODEL_URI`` accepts:

    s3://models/v1/                  directory/prefix (prefers model.tar.gz)
    s3://models/v1/model.tar.gz      a single tarball
    /local/path/to/model             local directory or tarball (tests/dev)

If the prefix contains ``model.sha256`` the tarball checksum is verified before
it is loaded.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import threading
from pathlib import Path

LOG = logging.getLogger("model-runner")


def _safe_extract(tar_path: Path, destination: Path) -> None:
    destination = destination.resolve()
    with tarfile.open(tar_path, "r:gz") as tar:
        for member in tar.getmembers():
            target = (destination / member.name).resolve()
            if not str(target).startswith(str(destination)):
                raise RuntimeError(f"unsafe path in model archive: {member.name}")
        tar.extractall(destination)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_model_dir(root: Path) -> Path:
    if (root / "config.json").is_file():
        return root
    for candidate in sorted(root.rglob("config.json")):
        if candidate.is_file():
            return candidate.parent
    raise RuntimeError(f"no config.json found under {root}")


class ModelRunner:
    def __init__(
        self,
        model_uri: str,
        model_version: str,
        s3_endpoint: str | None,
        max_new_tokens: int = 128,
        cache_dir: str | None = None,
    ):
        self.model_uri = model_uri
        self.model_version = model_version
        self.s3_endpoint = s3_endpoint
        self.max_new_tokens = max_new_tokens
        self.cache_dir = Path(cache_dir or os.environ.get("MODEL_CACHE_DIR", "/tmp/model"))
        self._lock = threading.Lock()
        self._model = None
        self._tokenizer = None
        self.model_dir: Path | None = None

    # ------------------------------------------------------------------ load
    def _download_from_s3(self) -> Path:
        from worker.storage import S3Store, parse_s3_uri

        bucket, key = parse_s3_uri(self.model_uri)
        store = S3Store(endpoint=self.s3_endpoint)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        if key.endswith(".tar.gz") or key.endswith(".tgz"):
            tar_key = key
            prefix = key.rsplit("/", 1)[0] + "/"
            local_tar = self.cache_dir / "model.tar.gz"
            LOG.info("downloading s3://%s/%s", bucket, tar_key)
            store.get_to_file(bucket, tar_key, local_tar)
        else:
            prefix = key.rstrip("/") + "/"
            tar_key = f"{prefix}model.tar.gz"
            keys = store.list(bucket, prefix)
            if not keys:
                raise RuntimeError(f"no objects under s3://{bucket}/{prefix}")
            if tar_key in keys:
                local_tar = self.cache_dir / "model.tar.gz"
                LOG.info("downloading s3://%s/%s", bucket, tar_key)
                store.get_to_file(bucket, tar_key, local_tar)
            else:
                LOG.info("downloading %d objects under s3://%s/%s", len(keys), bucket, prefix)
                for object_key in keys:
                    relative = object_key[len(prefix) :]
                    if not relative:
                        continue
                    store.get_to_file(bucket, object_key, self.cache_dir / relative)
                return _find_model_dir(self.cache_dir)

        # Verify model.sha256 when it is present next to the tarball.
        checksum_key = f"{prefix}model.sha256"
        checksum_data = store.get_bytes(bucket, checksum_key)
        if checksum_data:
            expected = checksum_data.decode("utf-8").split()[0]
            actual = _sha256(local_tar)
            if actual != expected:
                raise RuntimeError(
                    f"model checksum mismatch: expected {expected}, got {actual}"
                )
            LOG.info("model checksum verified: %s", actual)
        else:
            LOG.warning("no %s found; cannot verify tarball checksum", checksum_key)

        extract_dir = self.cache_dir / "extracted"
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)
        _safe_extract(local_tar, extract_dir)
        return _find_model_dir(extract_dir)

    def _resolve_model_dir(self) -> Path:
        if self.model_uri.startswith("s3://"):
            return self._download_from_s3()
        source = Path(self.model_uri)
        if source.is_dir():
            return _find_model_dir(source)
        if source.is_file() and source.suffix in {".gz", ".tgz"}:
            checksum_path = source.with_name("model.sha256")
            if checksum_path.is_file():
                expected = checksum_path.read_text(encoding="utf-8").split()[0]
                actual = _sha256(source)
                if actual != expected:
                    raise RuntimeError(
                        f"model checksum mismatch: expected {expected}, got {actual}"
                    )
                LOG.info("local model checksum verified: %s", actual)
            extract_dir = self.cache_dir / "extracted"
            if extract_dir.exists():
                shutil.rmtree(extract_dir)
            extract_dir.mkdir(parents=True)
            _safe_extract(source, extract_dir)
            return _find_model_dir(extract_dir)
        raise RuntimeError(f"MODEL_URI is neither an s3 URI nor a local model: {self.model_uri}")

    def load(self) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        torch.set_num_threads(int(os.environ.get("NUM_THREADS", "4")))
        self.model_dir = self._resolve_model_dir()
        LOG.info("loading model from %s", self.model_dir)
        tokenizer = AutoTokenizer.from_pretrained(self.model_dir)
        if tokenizer.pad_token is None:
            tokenizer.pad_token = tokenizer.eos_token
        model = AutoModelForCausalLM.from_pretrained(self.model_dir)
        model.eval()
        self._tokenizer = tokenizer
        self._model = model
        LOG.info(
            "model loaded: version=%s parameters=%d",
            self.model_version,
            sum(p.numel() for p in model.parameters()),
        )

    # --------------------------------------------------------------- predict
    def parse(self, text: str) -> str:
        if self._model is None or self._tokenizer is None:
            raise RuntimeError("model is not loaded")
        import torch

        prompt = f"Command: {text}\nJSON:"
        tokenizer = self._tokenizer
        with self._lock, torch.inference_mode():
            inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
            generated = self._model.generate(
                **inputs,
                max_new_tokens=self.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            new_tokens = generated[0][inputs["input_ids"].shape[1] :]
            return tokenizer.decode(new_tokens, skip_special_tokens=True)


def build_runner_from_env() -> ModelRunner:
    model_uri = os.environ.get("MODEL_URI")
    if not model_uri:
        raise RuntimeError("MODEL_URI is not set")
    return ModelRunner(
        model_uri=model_uri,
        model_version=os.environ.get("MODEL_VERSION", "v1"),
        s3_endpoint=os.environ.get("S3_ENDPOINT"),
        max_new_tokens=int(os.environ.get("MAX_NEW_TOKENS", "128")),
    )
