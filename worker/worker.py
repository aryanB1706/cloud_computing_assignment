#!/usr/bin/env python3
"""Sharded teacher-labelling worker (Task 1).

Each worker owns the commands whose zero-based line number satisfies

    line_number % SHARD_COUNT == SHARD_INDEX

and writes one JSON line per command to ``teacher/shard-<SHARD_INDEX>.jsonl``
in the ``datasets`` bucket:

    {"id": "...", "text": "...", "teacher_raw": "<exact Ollama reply>"}

The worker is resumable.  On start it re-reads its own shard object and skips
ids that are already present, so an interrupted multi-hour run can simply be
restarted (the Kubernetes Job re-runs it).  Progress is flushed to object
storage after every successful reply by default.

Environment
    SHARD_INDEX        zero-based shard index (required; JOB_COMPLETION_INDEX
                       is accepted as a fallback for Indexed Kubernetes Jobs)
    SHARD_COUNT        number of shards (required)
    S3_ENDPOINT        SeaweedFS S3 endpoint (s3 backend)
    STORAGE_BACKEND    s3 (default) or local
    LOCAL_STORAGE_DIR  root for the local backend
    TEACHER_URL        default http://localhost:11434
    TEACHER_MODEL      default phi3.5
    COMMANDS_KEY       default commands_pool.jsonl (in the datasets bucket)
    SHARD_KEY_PREFIX   default teacher/shard-
    PROMPT_PATH        default prompts/teacher_prompt.txt
    OLLAMA_TIMEOUT     seconds per request (default 600)
    MAX_RECORDS        optional smoke-test cap per shard
    FLUSH_EVERY        flush after N replies (default 1)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

import requests

try:  # allow both `python -m worker.worker` and `python worker/worker.py`
    from .storage import DATASETS_BUCKET, get_store, jsonl_bytes, read_jsonl
except ImportError:  # pragma: no cover - script mode
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from storage import DATASETS_BUCKET, get_store, jsonl_bytes, read_jsonl  # type: ignore

LOG = logging.getLogger("teacher-worker")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def load_prompt(path: str | None = None) -> str:
    path = path or os.environ.get("PROMPT_PATH", "prompts/teacher_prompt.txt")
    template = Path(path).read_text(encoding="utf-8")
    if "{command}" not in template:
        template = template.rstrip() + "\n\nCommand: {command}\nJSON:\n"
    return template


def select_shard(commands: list[dict], shard_index: int, shard_count: int) -> list[dict]:
    if not 0 <= shard_index < shard_count:
        raise ValueError(
            f"SHARD_INDEX must be in [0, {shard_count - 1}], got {shard_index}"
        )
    return [c for line, c in enumerate(commands) if line % shard_count == shard_index]


def render_prompt(prompt_template: str, command: str) -> str:
    """Substitute only the {command} placeholder, preserving JSON braces."""
    return prompt_template.replace("{command}", command)


def call_teacher(
    text: str,
    prompt_template: str,
    teacher_url: str,
    model: str,
    timeout: float,
    retries: int = 5,
) -> str:
    prompt = render_prompt(prompt_template, text)
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        # The assignment fixes temperature=0 and seed=42.
        "options": {
            "temperature": 0,
            "seed": 42,
            "num_predict": int(os.environ.get("TEACHER_NUM_PREDICT", "128")),
            "num_ctx": int(os.environ.get("TEACHER_NUM_CTX", "2048")),
            "top_k": 1,
        },
        "keep_alive": os.environ.get("TEACHER_KEEP_ALIVE", "30m"),
    }
    # The JSON reply is one line.  Stopping at a newline prevents the teacher
    # from continuing with extra commands or explanations; the exact first
    # line is still stored unchanged.  Set TEACHER_STOP="" to disable.
    stop = [value for value in os.environ.get("TEACHER_STOP", "\n").split("|") if value]
    if stop:
        payload["options"]["stop"] = stop
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{teacher_url.rstrip('/')}/api/generate",
                json=payload,
                timeout=(10, timeout),
            )
            resp.raise_for_status()
            body = resp.json()
            if "response" not in body:
                raise RuntimeError(f"Ollama response has no 'response' field: {body!r}")
            # Preserve exactly what the teacher generated.
            return body["response"]
        except Exception as exc:  # requests errors, JSON errors, HTTP errors
            last_error = exc
            wait = min(2 ** attempt, 30)
            LOG.warning(
                "teacher call failed (attempt %d/%d): %s; retrying in %ss",
                attempt,
                retries,
                exc,
                wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"teacher call failed after {retries} attempts: {last_error}")


def _id_sort_key(record: dict) -> tuple:
    raw = str(record.get("id", ""))
    digits = "".join(ch for ch in raw if ch.isdigit())
    return (int(digits) if digits else 0, raw)


def read_existing_shard(store, key: str) -> list[dict]:
    data = store.get_bytes(DATASETS_BUCKET, key)
    if not data:
        return []
    try:
        return read_jsonl(data)
    except json.JSONDecodeError:
        LOG.error("existing shard %s is corrupt; refusing to overwrite it", key)
        raise


def _write_shard(store, key: str, records: list[dict]) -> None:
    ordered = sorted(records, key=_id_sort_key)
    store.put_bytes(DATASETS_BUCKET, key, jsonl_bytes(ordered))


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the shard only")
    args = parser.parse_args(argv)

    shard_index_raw = os.environ.get("SHARD_INDEX") or os.environ.get(
        "JOB_COMPLETION_INDEX"
    )
    if shard_index_raw is None:
        parser.error("SHARD_INDEX (or JOB_COMPLETION_INDEX) must be set")
    if "SHARD_COUNT" not in os.environ:
        parser.error("SHARD_COUNT must be set")
    shard_index = int(shard_index_raw)
    shard_count = int(os.environ["SHARD_COUNT"])
    if shard_count < 1:
        parser.error("SHARD_COUNT must be >= 1")

    commands_key = os.environ.get("COMMANDS_KEY", "commands_pool.jsonl")
    shard_prefix = os.environ.get("SHARD_KEY_PREFIX", "teacher/shard-")
    shard_key = f"{shard_prefix}{shard_index}.jsonl"
    max_records = int(os.environ["MAX_RECORDS"]) if os.environ.get("MAX_RECORDS") else None
    flush_every = max(1, int(os.environ.get("FLUSH_EVERY", "1")))
    timeout = float(os.environ.get("OLLAMA_TIMEOUT", "600"))
    teacher_url = os.environ.get("TEACHER_URL", "http://localhost:11434")
    model = os.environ.get("TEACHER_MODEL", "phi3.5")

    store = get_store()
    payload = store.get_bytes(DATASETS_BUCKET, commands_key)
    if payload is None:
        raise SystemExit(
            f"commands object s3://{DATASETS_BUCKET}/{commands_key} not found; "
            "run scripts/init_storage.py first"
        )
    commands = read_jsonl(payload)
    shard = select_shard(commands, shard_index, shard_count)
    if max_records is not None:
        shard = shard[:max_records]

    LOG.info(
        "shard %d/%d: %d of %d commands, target bucket=%s key=%s backend=%s",
        shard_index,
        shard_count,
        len(shard),
        len(commands),
        DATASETS_BUCKET,
        shard_key,
        getattr(store, "backend", "unknown"),
    )
    if args.dry_run:
        print(json.dumps({"shard": shard_index, "count": len(shard), "ids": [c["id"] for c in shard]}))
        return 0

    prompt_template = load_prompt()
    existing = read_existing_shard(store, shard_key)
    completed = {str(r.get("id")) for r in existing}
    if completed:
        LOG.info("resuming: %d records already present in %s", len(completed), shard_key)
    output = list(existing)

    started = time.time()
    for position, command in enumerate(shard, 1):
        cid = str(command["id"])
        if cid in completed:
            continue
        raw = call_teacher(
            str(command["text"]), prompt_template, teacher_url, model, timeout
        )
        output.append({"id": cid, "text": command["text"], "teacher_raw": raw})
        completed.add(cid)
        if position % flush_every == 0:
            _write_shard(store, shard_key, output)
        LOG.info(
            "shard %d: %d/%d id=%s elapsed=%.1fs",
            shard_index,
            position,
            len(shard),
            cid,
            time.time() - started,
        )

    _write_shard(store, shard_key, output)
    expected = {str(c["id"]) for c in shard}
    missing = expected - completed
    if missing:
        LOG.error("shard %d still missing %d ids: %s", shard_index, len(missing), sorted(missing)[:10])
        return 2
    LOG.info(
        "shard %d complete: %d records in bucket=%s key=%s (%.1fs)",
        shard_index,
        len(output),
        DATASETS_BUCKET,
        shard_key,
        time.time() - started,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
