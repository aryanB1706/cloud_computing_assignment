"""Object-storage helpers shared by the labelling worker and the cleaner.

The assignment requires SeaweedFS (an S3-compatible store).  In deployment and
in CI the ``s3`` backend below is used.  A ``local`` backend exists purely so
that the pipeline can be unit-tested and smoke-tested on a laptop without a
cluster; it is never used by the Kubernetes manifests.

Object addresses use the same shape as the assignment:

    datasets bucket:  commands_pool.jsonl, teacher/shard-0.jsonl, v1/train.jsonl
    models   bucket:  v1/model.tar.gz, v2/model.tar.gz, ...

The S3 URL form ``s3://bucket/key`` is accepted by :func:`parse_s3_uri`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Iterable

DATASETS_BUCKET = os.environ.get("DATASETS_BUCKET", "datasets")
MODELS_BUCKET = os.environ.get("MODELS_BUCKET", "models")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    """Parse ``s3://bucket/key`` into ``(bucket, key)``."""
    if not uri.startswith("s3://"):
        raise ValueError(f"not an s3:// URI: {uri!r}")
    rest = uri[len("s3://") :]
    bucket, _, key = rest.partition("/")
    if not bucket:
        raise ValueError(f"missing bucket in {uri!r}")
    return bucket, key.lstrip("/")


class LocalStore:
    """Filesystem implementation of the small object-store interface.

    Layout: ``<root>/<bucket>/<key>``.  Writes are atomic (temp file + rename)
    so an interrupted process never leaves a half-written object.
    """

    backend = "local"

    def __init__(self, root: str | os.PathLike[str]):
        self.root = Path(root)

    def _path(self, bucket: str, key: str) -> Path:
        key = key.lstrip("/")
        return self.root / bucket / key

    def ensure_bucket(self, bucket: str) -> None:
        (self.root / bucket).mkdir(parents=True, exist_ok=True)

    def get_bytes(self, bucket: str, key: str) -> bytes | None:
        path = self._path(bucket, key)
        return path.read_bytes() if path.is_file() else None

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        path = self._path(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def put_file(self, bucket: str, key: str, path: str | os.PathLike[str]) -> None:
        self.put_bytes(bucket, key, Path(path).read_bytes())

    def get_to_file(self, bucket: str, key: str, path: str | os.PathLike[str]) -> None:
        data = self.get_bytes(bucket, key)
        if data is None:
            raise FileNotFoundError(f"s3://{bucket}/{key}")
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)

    def delete(self, bucket: str, key: str) -> None:
        path = self._path(bucket, key)
        if path.exists():
            path.unlink()

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        base = self._path(bucket, prefix)
        if base.is_file():
            return [prefix]
        if not base.exists():
            return []
        keys = []
        for p in base.rglob("*"):
            if p.is_file():
                keys.append(str(p.relative_to(self.root / bucket)).replace(os.sep, "/"))
        return sorted(keys)


class S3Store:
    """SeaweedFS / S3 backend using boto3.

    Credentials are read only from the standard AWS environment variables,
    which in Kubernetes are supplied by a Secret.
    """

    backend = "s3"

    def __init__(self, endpoint: str | None = None, region: str | None = None):
        import boto3
        from botocore.config import Config

        self.endpoint = endpoint or os.environ.get("S3_ENDPOINT")
        if not self.endpoint:
            raise RuntimeError(
                "S3_ENDPOINT is not set; point it at SeaweedFS, e.g. http://localhost:8333"
            )
        self.region = region or os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint,
            region_name=self.region,
            aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
            aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 5, "mode": "standard"},
                connect_timeout=10,
                read_timeout=120,
            ),
        )

    def ensure_bucket(self, bucket: str) -> None:
        from botocore.exceptions import ClientError

        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError:
            self.client.create_bucket(Bucket=bucket)

    def get_bytes(self, bucket: str, key: str) -> bytes | None:
        from botocore.exceptions import ClientError

        try:
            obj = self.client.get_object(Bucket=bucket, Key=key)
            return obj["Body"].read()
        except ClientError as exc:
            code = str(exc.response.get("Error", {}).get("Code", ""))
            if code in {"NoSuchKey", "404", "NoSuchBucket", "NotFound"}:
                return None
            raise

    def put_bytes(self, bucket: str, key: str, data: bytes) -> None:
        self.client.put_object(Bucket=bucket, Key=key, Body=data)

    def put_file(self, bucket: str, key: str, path: str | os.PathLike[str]) -> None:
        self.client.upload_file(str(path), bucket, key)

    def get_to_file(self, bucket: str, key: str, path: str | os.PathLike[str]) -> None:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.client.download_file(bucket, key, str(dest))

    def delete(self, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)

    def list(self, bucket: str, prefix: str = "") -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = self.client.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return sorted(keys)


def get_store():
    """Create the store selected by ``STORAGE_BACKEND`` (default: s3)."""
    backend = os.environ.get("STORAGE_BACKEND", "s3").lower()
    if backend == "local":
        root = os.environ.get("LOCAL_STORAGE_DIR", "./local-storage")
        return LocalStore(root)
    if backend == "s3":
        return S3Store()
    raise ValueError(f"unknown STORAGE_BACKEND={backend!r} (expected s3 or local)")


def split_lines(data: bytes) -> Iterable[str]:
    for line in data.decode("utf-8").splitlines():
        if line.strip():
            yield line


def read_jsonl(data: bytes) -> list[dict]:
    import json

    return [json.loads(line) for line in split_lines(data)]


def jsonl_bytes(rows: Iterable[dict]) -> bytes:
    import json

    return (
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)
    ).encode("utf-8")


def copy_tree_to_store(store, bucket: str, prefix: str, directory: str | os.PathLike[str]) -> list[str]:
    """Upload every regular file below ``directory`` under ``prefix``."""
    base = Path(directory)
    uploaded: list[str] = []
    for path in sorted(p for p in base.rglob("*") if p.is_file()):
        rel = path.relative_to(base).as_posix()
        key = f"{prefix.rstrip('/')}/{rel}" if prefix else rel
        store.put_file(bucket, key, path)
        uploaded.append(key)
    return uploaded


def download_prefix(store, bucket: str, prefix: str, directory: str | os.PathLike[str]) -> list[str]:
    """Download every object under ``prefix`` into ``directory``."""
    base = Path(directory)
    base.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    for key in store.list(bucket, prefix):
        rel = key[len(prefix) :].lstrip("/") if prefix else key
        if not rel:
            continue
        store.get_to_file(bucket, key, base / rel)
        downloaded.append(rel)
    return downloaded
