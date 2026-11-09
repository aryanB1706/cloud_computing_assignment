#!/usr/bin/env python3
"""Generate predictions for score.py (Task 2 step 6).

Modes
    student : a trained or untrained Hugging Face causal LM
    teacher : Ollama phi3.5 with the Task-1 prompt, temperature 0, seed 42

Output is exactly what score.py expects:

    {"id": "V001", "prediction": "<raw text the model produced>"}

Examples
    python training/predict.py --kind student --model models/v1 \
        --input data/validation.jsonl --output results/pred_student_v1.jsonl
    python training/predict.py --kind student --model EleutherAI/pythia-70m \
        --output results/pred_base.jsonl
    python training/predict.py --kind teacher \
        --output results/pred_teacher.jsonl
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from worker.storage import read_jsonl  # noqa: E402

LOG = logging.getLogger("predict")


def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def load_rows(input_path: str, limit: int | None, dataset_key: str | None) -> list[dict]:
    if dataset_key:
        from worker.storage import DATASETS_BUCKET, get_store

        data = get_store().get_bytes(DATASETS_BUCKET, dataset_key)
        if data is None:
            raise SystemExit(f"s3://{DATASETS_BUCKET}/{dataset_key} not found")
        rows = read_jsonl(data)
    else:
        rows = read_jsonl(Path(input_path).read_bytes())
    return rows[:limit] if limit else rows


def extract_if_tarball(model_path: str) -> str:
    path = Path(model_path)
    if not path.is_file() or path.suffix not in {".gz", ".tgz"}:
        return model_path
    target = Path(tempfile.mkdtemp(prefix="model-"))
    with tarfile.open(path, "r:gz") as tar:
        for member in tar.getmembers():
            member_path = (target / member.name).resolve()
            if not str(member_path).startswith(str(target.resolve())):
                raise SystemExit(f"unsafe path in {path}: {member.name}")
        tar.extractall(target)
    LOG.info("extracted %s to %s", path, target)
    return str(target)


def student_predictions(args, rows: list[dict]) -> list[dict]:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model_path = extract_if_tarball(args.model)
    LOG.info("loading student %s", model_path)
    tokenizer = AutoTokenizer.from_pretrained(model_path)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(model_path)
    model.eval()
    torch.set_num_threads(int(os.environ.get("NUM_THREADS", "8")))

    predictions = []
    started = time.time()
    with torch.no_grad():
        for index, row in enumerate(rows, 1):
            prompt = f"Command: {row['text']}\nJSON:"
            inputs = tokenizer(prompt, return_tensors="pt", add_special_tokens=False)
            generated = model.generate(
                **inputs,
                max_new_tokens=args.max_new_tokens,
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
                eos_token_id=tokenizer.eos_token_id,
            )
            new_tokens = generated[0][inputs["input_ids"].shape[1] :]
            raw = tokenizer.decode(new_tokens, skip_special_tokens=True).strip()
            predictions.append({"id": row["id"], "prediction": raw})
            if index % 10 == 0 or index == len(rows):
                LOG.info("%d/%d predictions (%.1fs)", index, len(rows), time.time() - started)
    return predictions


def teacher_predictions(args, rows: list[dict]) -> list[dict]:
    from worker.worker import call_teacher, load_prompt

    prompt_template = load_prompt(args.prompt_path)
    predictions = []
    started = time.time()
    for index, row in enumerate(rows, 1):
        raw = call_teacher(
            str(row["text"]),
            prompt_template,
            args.ollama_url,
            args.model,
            timeout=float(args.timeout),
        )
        predictions.append({"id": row["id"], "prediction": raw})
        if index % 5 == 0 or index == len(rows):
            LOG.info("%d/%d teacher predictions (%.1fs)", index, len(rows), time.time() - started)
    return predictions


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kind", choices=["student", "teacher"], default="student")
    parser.add_argument("--model", default=None, help="HF id, local directory, or tar.gz")
    parser.add_argument("--input", default="data/validation.jsonl")
    parser.add_argument(
        "--dataset-key",
        default=None,
        help="read the input from s3://datasets/<key> instead of a local file",
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-new-tokens", type=int, default=128)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--prompt-path", default=os.environ.get("PROMPT_PATH", "prompts/teacher_prompt.txt")
    )
    parser.add_argument("--ollama-url", default=os.environ.get("TEACHER_URL", "http://localhost:11434"))
    parser.add_argument("--timeout", default=os.environ.get("OLLAMA_TIMEOUT", "600"))
    args = parser.parse_args(argv)

    if args.model is None:
        args.model = "phi3.5" if args.kind == "teacher" else "EleutherAI/pythia-70m"

    rows = load_rows(args.input, args.limit, args.dataset_key)
    LOG.info("predicting %d examples with %s (%s)", len(rows), args.model, args.kind)
    predictions = (
        teacher_predictions(args, rows) if args.kind == "teacher" else student_predictions(args, rows)
    )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        for prediction in predictions:
            fh.write(json.dumps(prediction, ensure_ascii=False) + "\n")
    LOG.info("wrote %d predictions to %s", len(predictions), output)
    print(json.dumps({"output": str(output), "count": len(predictions)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
