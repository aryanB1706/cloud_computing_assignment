#!/usr/bin/env python3
"""Sequence-level knowledge distillation of EleutherAI/pythia-70m (Task 2).

Every training example is the teacher's exact reply, conditioned on the command:

    Command: <command>
    JSON: <teacher JSON>

Usage
    python training/train.py --config training/config.yaml
    SUBSET=50 python training/train.py --config training/config.yaml

The script reads hyperparameters from the YAML file only (SUBSET and the
storage location are the only environment/CLI inputs).  It writes one loss
value per epoch to the configured CSV, saves the model and tokenizer, creates a
reproducible ``model.tar.gz`` with a SHA-256 checksum, and uploads all artefacts
to ``models/<version>/`` in object storage.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import logging
import os
import random
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "worker"))

from storage import get_store, read_jsonl  # noqa: E402

LOG = logging.getLogger("train")
EXPECTED_KEYS = {"intent", "date", "time", "place"}


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def load_config(path: str | Path) -> dict:
    import yaml

    with open(path, encoding="utf-8") as fh:
        config = yaml.safe_load(fh)
    required = {"seed", "learning_rate", "batch_size", "epochs", "max_seq_len", "libraries"}
    missing = required - set(config)
    if missing:
        raise SystemExit(f"{path}: missing required keys: {sorted(missing)}")
    return config


def set_seeds(seed: int) -> None:
    random.seed(seed)
    os.environ.setdefault("PYTHONHASHSEED", str(seed))
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:  # pragma: no cover
        pass
    import torch

    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def read_training_rows(config: dict, subset: int | None, input_path: str | None) -> tuple[list[dict], str]:
    if input_path:
        rows = read_jsonl(Path(input_path).read_bytes())
        source = input_path
    else:
        store = get_store()
        bucket = config["data"].get("bucket", "datasets")
        key = config["data"]["key"]
        payload = store.get_bytes(bucket, key)
        if payload is None:
            raise SystemExit(
                f"training data s3://{bucket}/{key} not found; run worker/clean.py first"
            )
        rows = read_jsonl(payload)
        source = f"s3://{bucket}/{key}"

    limit = subset if subset is not None else int(config["data"]["max_train_records"])
    rows = rows[:limit]
    if not rows:
        raise SystemExit("no training rows selected")

    # Defensive checks: training must use teacher JSON and never validation ids.
    for row in rows:
        identifier = str(row.get("id", ""))
        if identifier.upper().startswith("V"):
            raise SystemExit(f"validation-like id {identifier} found in training data")
        if "text" not in row or "teacher_raw" not in row:
            raise SystemExit(f"training record {identifier} lacks text/teacher_raw")
        try:
            parsed = json.loads(str(row["teacher_raw"]).strip())
        except json.JSONDecodeError as exc:
            raise SystemExit(f"training record {identifier} has invalid teacher JSON: {exc}")
        if set(parsed) != EXPECTED_KEYS:
            raise SystemExit(f"training record {identifier} has wrong keys: {sorted(parsed)}")
    return rows, source


def build_example(row: dict) -> dict:
    """Return the prompt and the sequence-level distillation target."""
    teacher_json = str(row["teacher_raw"]).strip()
    prompt = f"Command: {row['text']}\nJSON:"
    return {"id": row.get("id"), "prompt": prompt, "target": teacher_json}


def collate_batch(batch: list[dict], pad_token_id: int) -> dict:
    import torch

    max_len = max(len(item["input_ids"]) for item in batch)
    input_ids, labels, attention = [], [], []
    for item in batch:
        ids = item["input_ids"]
        pad = max_len - len(ids)
        input_ids.append(ids + [pad_token_id] * pad)
        labels.append(list(item["labels"]) + [-100] * pad)
        attention.append([1] * len(ids) + [0] * pad)
    return {
        "input_ids": torch.tensor(input_ids, dtype=torch.long),
        "labels": torch.tensor(labels, dtype=torch.long),
        "attention_mask": torch.tensor(attention, dtype=torch.long),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_reproducible_tar(source_dir: Path, tar_path: Path) -> None:
    """Create a gzip tarball with normalised metadata (stable checksums)."""
    excluded = {tar_path.name, "model.sha256"}
    files = sorted(
        p for p in source_dir.rglob("*") if p.is_file() and p.name not in excluded
    )
    with open(tar_path, "wb") as raw:
        with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
            with tarfile.open(fileobj=gz, mode="w") as tar:
                for path in files:
                    info = tar.gettarinfo(str(path), arcname=path.relative_to(source_dir).as_posix())
                    info.mtime = 0
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    with open(path, "rb") as fh:
                        tar.addfile(info, fh)


def write_checksums(directory: Path) -> Path:
    lines = []
    for path in sorted(p for p in directory.rglob("*") if p.is_file()):
        if path.name in {"model.tar.gz", "model.sha256", "checksums.txt"}:
            continue
        lines.append(f"{sha256_file(path)}  {path.relative_to(directory).as_posix()}")
    out = directory / "checksums.txt"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def observed_versions() -> dict:
    from importlib import metadata

    packages = {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "torch": None,
        "transformers": None,
        "tokenizers": None,
        "safetensors": None,
        "huggingface_hub": None,
        "numpy": None,
        "pyyaml": None,
        "boto3": None,
    }
    names = {
        "torch": "torch",
        "transformers": "transformers",
        "tokenizers": "tokenizers",
        "safetensors": "safetensors",
        "huggingface_hub": "huggingface-hub",
        "numpy": "numpy",
        "pyyaml": "PyYAML",
        "boto3": "boto3",
    }
    for key, package in names.items():
        try:
            packages[key] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[key] = "not-installed"
    return packages


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("CONFIG", "training/config.yaml"))
    parser.add_argument("--input", default=os.environ.get("TRAIN_INPUT"), help="local train.jsonl (overrides object storage)")
    parser.add_argument("--subset", type=int, default=None, help="use only the first N records (smoke test)")
    parser.add_argument("--output-dir", default=None, help="override models/<version>")
    parser.add_argument("--no-upload", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="load and validate data only")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    subset = args.subset
    if subset is None and os.environ.get("SUBSET"):
        subset = int(os.environ["SUBSET"])
    seed = int(os.environ.get("SEED", config["seed"]))
    version = os.environ.get("VERSION", config["training"].get("version", "v1"))
    learning_rate = float(config["learning_rate"])
    batch_size = int(config["batch_size"])
    epochs = int(os.environ.get("EPOCHS", config["epochs"]))
    max_seq_len = int(config["max_seq_len"])

    rows, data_source = read_training_rows(config, subset, args.input)
    LOG.info(
        "training data: %d records from %s (subset=%s, version=%s)",
        len(rows),
        data_source,
        subset,
        version,
    )
    if args.dry_run:
        print(json.dumps({"records": len(rows), "source": data_source, "version": version}, indent=2))
        return 0

    import torch
    from torch.utils.data import DataLoader
    from transformers import AutoModelForCausalLM, AutoTokenizer, get_linear_schedule_with_warmup

    torch.set_num_threads(int(config["training"].get("num_threads", 8)))
    set_seeds(seed)

    model_name = config["model"]["name"]
    revision = config["model"].get("revision", "main")
    LOG.info("loading tokenizer/model %s@%s", model_name, revision)
    tokenizer = AutoTokenizer.from_pretrained(model_name, revision=revision)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    tokenizer.padding_side = "right"
    eos_id = tokenizer.eos_token_id

    examples = [build_example(row) for row in rows]
    if config["training"].get("shuffle", True):
        random.Random(seed).shuffle(examples)

    encoded = []
    for example in examples:
        prompt_ids = tokenizer(example["prompt"], add_special_tokens=False)["input_ids"]
        target_ids = tokenizer(example["target"], add_special_tokens=False)["input_ids"]
        target_ids = (target_ids + [eos_id])[:max_seq_len]
        # Mask the prompt: the loss is computed only on the teacher reply.
        prompt_ids = prompt_ids[: max(0, max_seq_len - len(target_ids))]
        ids = prompt_ids + target_ids
        labels = [-100] * len(prompt_ids) + target_ids
        if not target_ids:
            raise SystemExit(f"training record {example['id']} produced no target tokens")
        encoded.append({"id": example["id"], "input_ids": ids, "labels": labels})
    lengths = [len(x["input_ids"]) for x in encoded]
    LOG.info(
        "tokenised %d examples; tokens min=%d mean=%.1f max=%d",
        len(encoded),
        min(lengths),
        sum(lengths) / len(lengths),
        max(lengths),
    )

    model = AutoModelForCausalLM.from_pretrained(model_name, revision=revision)
    model.config.pad_token_id = tokenizer.pad_token_id
    model.train()

    loader = DataLoader(
        encoded,
        batch_size=batch_size,
        shuffle=False,
        collate_fn=lambda batch: collate_batch(batch, tokenizer.pad_token_id),
    )
    grad_accum = max(1, int(config["training"].get("gradient_accumulation_steps", 1)))
    optimizer = torch.optim.AdamW(
        model.parameters(),
        lr=learning_rate,
        weight_decay=float(config["training"].get("weight_decay", 0.0)),
    )
    steps_per_epoch = max(1, (len(encoded) + batch_size - 1) // batch_size)
    optimizer_steps_per_epoch = max(1, (steps_per_epoch + grad_accum - 1) // grad_accum)
    total_steps = optimizer_steps_per_epoch * epochs
    warmup_steps = int(total_steps * float(config["training"].get("warmup_ratio", 0.0)))
    scheduler = get_linear_schedule_with_warmup(optimizer, warmup_steps, total_steps)

    output_dir = Path(args.output_dir) if args.output_dir else Path(config["training"]["output_dir"]) / version
    output_dir.mkdir(parents=True, exist_ok=True)
    loss_csv = Path(config["training"].get("loss_csv", "training/loss.csv"))
    loss_csv.parent.mkdir(parents=True, exist_ok=True)
    loss_history: list[dict] = []
    started = time.time()

    with open(loss_csv, "w", newline="", encoding="utf-8") as loss_fh:
        writer = csv.writer(loss_fh)
        writer.writerow(["epoch", "loss", "optimizer_steps", "examples", "tokens", "elapsed_s"])
        loss_fh.flush()

        for epoch in range(1, epochs + 1):
            epoch_started = time.time()
            total_loss = 0.0
            total_tokens = 0
            optimizer.zero_grad(set_to_none=True)
            micro_step = 0
            for batch_index, batch in enumerate(loader, 1):
                outputs = model(**batch)
                loss = outputs.loss
                n_tokens = int((batch["labels"] != -100).sum().item())
                (loss / grad_accum).backward()
                total_loss += float(loss.item()) * n_tokens
                total_tokens += n_tokens
                micro_step += 1
                if micro_step % grad_accum == 0:
                    torch.nn.utils.clip_grad_norm_(
                        model.parameters(), float(config["training"].get("max_grad_norm", 1.0))
                    )
                    optimizer.step()
                    scheduler.step()
                    optimizer.zero_grad(set_to_none=True)
                if batch_index % int(config["training"].get("logging_steps", 10)) == 0:
                    LOG.info(
                        "epoch %d batch %d/%d loss=%.4f lr=%.2e",
                        epoch,
                        batch_index,
                        steps_per_epoch,
                        loss.item(),
                        scheduler.get_last_lr()[0],
                    )
            # Flush the final partial accumulation window.
            if micro_step % grad_accum != 0:
                torch.nn.utils.clip_grad_norm_(
                    model.parameters(), float(config["training"].get("max_grad_norm", 1.0))
                )
                optimizer.step()
                scheduler.step()
                optimizer.zero_grad(set_to_none=True)

            epoch_loss = total_loss / max(1, total_tokens)
            elapsed = time.time() - started
            row = {
                "epoch": epoch,
                "loss": round(epoch_loss, 6),
                "optimizer_steps": optimizer_steps_per_epoch * epoch,
                "examples": len(encoded),
                "tokens": total_tokens,
                "elapsed_s": round(elapsed, 1),
            }
            loss_history.append(row)
            writer.writerow([row[k] for k in ("epoch", "loss", "optimizer_steps", "examples", "tokens", "elapsed_s")])
            loss_fh.flush()
            os.fsync(loss_fh.fileno())
            LOG.info("epoch %d/%d loss=%.4f (%.1fs)", epoch, epochs, epoch_loss, time.time() - epoch_started)

    LOG.info("saving model and tokenizer to %s", output_dir)
    model.save_pretrained(output_dir, safe_serialization=True)
    tokenizer.save_pretrained(output_dir)

    metadata = {
        "model": model_name,
        "revision": revision,
        "version": version,
        "format": "Command: <command>\\nJSON: <teacher reply>",
        "config_file": str(args.config),
        "data_source": data_source,
        "records_used": len(rows),
        "subset": subset,
        "seed": seed,
        "learning_rate": learning_rate,
        "batch_size": batch_size,
        "gradient_accumulation_steps": grad_accum,
        "epochs": epochs,
        "max_seq_len": max_seq_len,
        "parameters": sum(p.numel() for p in model.parameters()),
        "loss_history": loss_history,
        "libraries_requested": config["libraries"],
        "libraries_observed": observed_versions(),
        "training_seconds": round(time.time() - started, 1),
    }
    (output_dir / "training_metadata.json").write_text(
        json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
    )
    write_checksums(output_dir)
    tar_path = output_dir / "model.tar.gz"
    make_reproducible_tar(output_dir, tar_path)
    checksum = sha256_file(tar_path)
    checksum_line = f"{checksum}  model.tar.gz\n"
    (output_dir / "model.sha256").write_text(checksum_line, encoding="utf-8")
    # Keep a copy of the loss curve alongside the model artefacts.
    (output_dir / "loss.csv").write_bytes(loss_csv.read_bytes())

    if not args.no_upload:
        store = get_store()
        models_bucket = os.environ.get("MODELS_BUCKET", "models")
        uploaded = []
        for path in sorted(p for p in output_dir.rglob("*") if p.is_file()):
            key = f"{version}/{path.relative_to(output_dir).as_posix()}"
            store.put_file(models_bucket, key, path)
            uploaded.append(key)
        LOG.info("uploaded %d artefacts to s3://%s/%s/", len(uploaded), models_bucket, version)

    print(
        json.dumps(
            {
                "version": version,
                "records": len(rows),
                "epochs": epochs,
                "final_loss": loss_history[-1]["loss"] if loss_history else None,
                "output_dir": str(output_dir),
                "model_sha256": checksum,
                "loss_csv": str(loss_csv),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
