# AI use declaration

This project was developed with AI coding assistance. This file records which
tools were used, what they were used for, and how the output was checked. It is
deliberately factual: only commands and results that were actually executed are
reported as measured.

## Tools used

| Tool | Where it was used | What it produced |
|---|---|---|
| opencode (CLI coding agent, model `deepseek-v4.1-flash-free`) | Repository scaffolding, Python/PyTorch/FastAPI code, Kubernetes manifests, tests, CI workflow, documentation drafts | Most source files in this repository |
| The official `student_kit` scripts (`tools/score.py`, `tools/nacl_sim.py`, `tools/locustfile.py`) | Evaluation and validation | Scoring, NACL decisions and load-test measurements; these scripts are provided course material, not AI output |
| Ollama + `phi3.5` | Task 1 teacher labelling | Teacher replies, stored verbatim; the prompt was written for this project |

No paid inference API or paid cloud service was used.

## How AI output was checked

1. **Tests.** The repository has unit and integration tests (`tests/`) covering
   cleaning/accounting, shard partitioning, subnet arithmetic, NACL decisions,
   service validation and failures, cost arithmetic, and object-storage
   behaviour. `pytest -q` was run locally; the exact output is in
   `results/pytest.txt`.
2. **Official tools.** The NACL file was checked with the provided
   `tools/nacl_sim.py` (6/6 sample packets). Predictions are scored with the
   provided `tools/score.py`; no custom scoring logic is used for reported
   accuracy.
3. **Linting.** `ruff check .` is part of CI and was run locally; output is in
   `results/lint.txt`.
4. **Manual review of every generated file.** The manifests were checked for
   the paths, ports, environment variables, probe endpoints and resource values
   required by the assignment. The training script records the exact library
   versions it observed in `training_metadata.json`.
5. **No fabricated measurements.** The report and `results/` clearly separate:
   - results actually measured on this laptop (tests, NACL simulation, local
     service/load runs when available), and
   - requirements that could not be executed in the available environment
     (k3d/Kubernetes and full 3,000-command teacher labelling); those are marked
     as not measured rather than estimated.

## Prompt and model provenance

- Teacher model: `phi3.5` served by Ollama, `temperature=0`, `seed=42`.
- Teacher prompt: `prompts/teacher_prompt.txt` (written for this project).
- Student base model: `EleutherAI/pythia-70m`.
- Training targets: only the teacher's raw replies, cleaned by
  `worker/clean.py`. The validation set is never used for training.

## Author checklist before submission

- [ ] Re-run `make test`, `make nacl`, and the local service/load tests.
- [ ] Confirm `results/` contains only real, reproduced measurements.
- [ ] Confirm the GitHub Release checksum matches `models/v1/model.sha256`.
- [ ] Confirm the quality-gate demo run link is in the report.
- [ ] Confirm no secrets are present (`gitleaks detect ...` reports nothing).
- [ ] Write the hand-written reflection and upload it as `<roll_number>.pdf`.
