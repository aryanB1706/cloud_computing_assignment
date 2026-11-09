# Knowledge Distillation and Deployment of a Lightweight Command-Parsing LM

**GitHub repository:** [aryanB1706/cloud_computing_assignment](https://github.com/aryanB1706/cloud_computing_assignment)<br>
**Course:** CS3081 Cloud Computing<br>
**Student:** Aryan Kumar — **Roll number:** 230101050

Submission files are prepared separately: the project report is
[`report/report.pdf`](report/report.pdf), and the reflection must be handwritten
and scanned by the student as `230101050.pdf` (maximum three pages). The final
upload package should be named `230101050.zip`; do not include model weights
from the ignored `models/` directory because the model package is delivered by
the GitHub Release.

A local-cloud implementation of the CS3081 mini project. A `phi3.5` teacher
labels commands, the replies distil into `EleutherAI/pythia-70m`, and the
student is served by FastAPI on a k3d Kubernetes cluster with SeaweedFS object
storage, Traefik ingress, probes, an HPA, and a Locust performance test.

## Architecture

```
                      Ollama phi3.5 (host, temperature=0, seed=42)
                                   │
             worker/worker.py (SHARD_INDEX) x N parallel Kubernetes Jobs
                                   │  {"id","text","teacher_raw"}
                                   ▼
  datasets bucket ──► worker/clean.py ──► datasets/v1/train.jsonl (+ sha256)
        (SeaweedFS)                                   │
                                             training/train.py (CPU)
                                                      │
                                   models/v1, models/v2 (model.tar.gz + sha256)
                                                      │
                          ┌───────────────────────────┴────────────────┐
                          │  FastAPI service (no weights in image)     │
                          │  downloads model from SeaweedFS at startup │
                          └───────────────────────────┬────────────────┘
                                                      │
                     namespace app: Deployment (2) + Service + Ingress + HPA
                                                      │
                                       Locust -> Traefik -> /parse
```

## Repository layout

```
cluster/k3d.yaml          k3d cluster definition
data/                     official kit data + schema + rate card
prompts/teacher_prompt.txt
worker/                   shard worker, cleaner, storage abstraction
training/                 config.yaml, CPU training, prediction/evaluation
service/                  FastAPI app, validator, model downloader, Dockerfile
k8s/                      namespaces, SeaweedFS, app, ingress, HPA, worker Job
network/                  subnets.md, subnets.json, nacl.json
tools/                    provided kit scripts (score, nacl_sim, locustfile)
tests/                    unit + integration tests
scripts/                  up/down, storage init, evaluate, cost, benchmark
results/                  evidence produced by real runs
report/                   report sources and PDF
.github/workflows/ci.yml  lint -> tests -> eval -> quality gate + gitleaks
```

The files in `data/`, `tools/` and `network/packets_sample.jsonl` are the
provided course kit (see `data/README.md`), not model output.

## Prerequisites

- Linux or macOS with Docker (the daemon must be running)
- `k3d` >= 5.6 and `kubectl`
- Python 3.12
- `ollama` with the fixed `phi3.5` model (`ollama pull phi3.5`)
- About 8 GiB of free RAM for the teacher; CPU-only training is fine
- `gh` (optional, for Release download/publish) and `gitleaks` (optional, for
  local secret scanning)

The project never uses a paid API or paid cloud service.

## Quick start (fresh clone, at most three commands)

The model Release asset is the required deliverable, so a fresh clone can fetch
it:

```bash
make install-ml           # 1. Python environment (CPU torch + test deps)
make up                   # 2. cluster + storage + images + Release model + deploy
```

`make up` performs the remaining steps (namespaces, secrets, SeaweedFS,
buckets/data, image build/import, model upload, Deployment/Service/Ingress/HPA).
If your Release asset is not named under tag `final`, set
`GITHUB_REPO=owner/repo RELEASE_TAG=<tag>`.

If you prefer to train locally before deploying:

```bash
make install-ml
# Ollama must listen on all interfaces so the worker pods can reach it:
OLLAMA_HOST=0.0.0.0:11434 ollama serve &
ollama pull phi3.5
make teacher-label        # resumable Kubernetes Indexed Job
make clean                # shards -> datasets/v1/train.jsonl + checksum
make train                # v1 (first 1,500 records)
make train-v2             # v2 (seed 43, 3 epochs)
make up                   # uploads models/ automatically
```

Teardown:

```bash
make down                 # k3d cluster delete kd-cloud
```

## Task 1 - object storage and parallel teacher labelling

Cluster creation uses `cluster/k3d.yaml` (Traefik and metrics-server stay
enabled). SeaweedFS runs in namespace `data`; the two buckets are `datasets`
and `models`.

```bash
# Host-side credentials/config: generated once, stored in git-ignored .env.local
kubectl apply -f k8s/00-namespaces.yaml
bash scripts/create_secrets.sh
kubectl apply -f k8s/seaweedfs.yaml
kubectl -n data rollout status deploy/seaweedfs

# Create buckets and upload data/commands_pool.jsonl
make storage-init

# Teacher: run Ollama on the host, bound to all interfaces so pods can reach it.
# On a laptop, restrict port 11434 with a firewall; it is not exposed publicly.
OLLAMA_HOST=0.0.0.0:11434 ollama serve &
ollama pull phi3.5
make teacher-label
kubectl -n pipeline get pods -w
kubectl -n pipeline logs -f job/teacher-labelling

# Clean and checksum
make clean
```

Workers read `SHARD_INDEX` (falling back to `JOB_COMPLETION_INDEX`) and
`SHARD_COUNT`. The Indexed Job sets both and runs two pods at a time. Each
worker writes only its own object, `datasets/teacher/shard-<i>.jsonl`, with
`{"id","text","teacher_raw"}`. Re-running the Job resumes: existing ids in the
shard object are skipped, and progress is flushed after every reply.

`worker/clean.py` removes (a) invalid JSON, (b) wrong keys, (c) unknown intent
and (d) ungrounded/typed slots, then removes duplicate commands by normalised
text (keeping the smallest id), sorts by id, and uploads
`datasets/v1/train.jsonl`, `datasets/v1/train.sha256` and
`datasets/v1/clean_counts.json`. The accounting invariant is asserted:

```
input = invalid_json + wrong_keys + bad_intent + bad_slot + duplicates + kept
```

## Task 2 - sequence-level distillation

`training/config.yaml` pins all six required items (seed 42, learning rate
5e-5, batch size 4, 2 epochs, max sequence length 256, library versions). The
second version is `training/config_v2.yaml`.

```bash
make train                 # v1, reads only training/config.yaml
make train SUBSET=50       # smoke test
make train-v2              # v2
```

Each example is exactly:

```
Command: <command>
JSON: <teacher JSON reply>
```

`training/loss.csv` has one loss value per epoch (plus step/token counts). The
script saves the model + tokenizer, `training_metadata.json`, `loss.csv`, a
reproducible `model.tar.gz`, per-file `checksums.txt`, and `model.sha256`
(checksum of the tarball). Artefacts go to `models/<version>/` in SeaweedFS.

Evaluation:

```bash
make evaluate              # teacher, untrained Pythia, student v1, student v2
SKIP_TEACHER=1 make evaluate   # if Ollama is unavailable
```

Raw predictions and score reports are written to `results/`:
`pred_teacher.jsonl`, `pred_base.jsonl`, `pred_student_v1.jsonl`,
`pred_student_v2.jsonl`, `teacher_validation.json`, `score_base.json`,
`score_student_v1.json`, `score_student_v2.json`, `evaluation_summary.json`.

### Publish the v1 Release asset

```bash
gh release create final models/v1/model.tar.gz models/v1/model.sha256 \
  --title "final model v1" --notes "Pythia-70M distilled from phi3.5"
```

The Release asset is `model.tar.gz`; `model.sha256` contains its SHA-256.

## Task 3 - inference service and container

The service listens on port 8000:

```bash
.venv/bin/uvicorn service.app:app --host 0.0.0.0 --port 8000
```

Environment (all required values; credentials through a Secret in Kubernetes):

| Variable | Meaning |
|---|---|
| `MODEL_URI` | `s3://models/v1/model.tar.gz` (or prefix/directory) |
| `MODEL_VERSION` | `v1` or `v2`, returned in `X-Model-Version` |
| `S3_ENDPOINT` | SeaweedFS S3 URL |
| `MAX_NEW_TOKENS` | generation cap |
| `FAULT_INJECT` | `1` replaces model output with `not json` |
| `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` | from Kubernetes Secret |

Endpoints: `POST /parse`, `GET /healthz`, `GET /readyz`. `/parse` returns exactly
the four keys and sets `X-Model-Version` and `X-Served-By` (pod name). Invalid
output returns HTTP 500 with exactly `{"error":"invalid_model_output"}`.

Build the pinned CPU-only image (no weights inside):

```bash
make image                 # docker build -f service/Dockerfile -t command-parser-service:0.1.0 .
docker run --rm --cpus=1 --memory=1g -p 8000:8000 \
  -e MODEL_URI=... -e MODEL_VERSION=v1 -e S3_ENDPOINT=... -e MAX_NEW_TOKENS=128 \
  -e AWS_ACCESS_KEY_ID=... -e AWS_SECRET_ACCESS_KEY=... command-parser-service:0.1.0
```

The 50-request sequential test and peak-RSS report:

```bash
python scripts/bench_local.py --url http://127.0.0.1:8000 --n 50 --pid <uvicorn-pid>
```

## Task 4 - Kubernetes deployment, scaling and reliability

```bash
kubectl -n app get deploy,svc,ingress,hpa,pods
kubectl -n app logs deploy/command-parser | grep -i download
```

The Deployment starts with two replicas, CPU request 250m / limit 1, memory
request 512Mi / limit 1Gi, liveness `/healthz`, readiness `/readyz`, rolling
updates with `maxUnavailable: 0`. The HPA scales 1-2 replicas at 70% CPU.
`docs/RUNBOOK.md` contains the exact commands for scaling, rolling update,
rollback, pod deletion, cold-start timing and load-balancing evidence.

## Task 5 - subnets and NACL

See `network/subnets.md` and `network/subnets.json`:

| Subnet | CIDR | Usable range | Broadcast | Usable |
|---|---|---|---|---|
| app | 10.20.0.0/22 | 10.20.0.1-10.20.3.254 | 10.20.3.255 | 1022 |
| public | 10.20.4.0/24 | 10.20.4.1-10.20.4.254 | 10.20.4.255 | 254 |
| data | 10.20.5.0/25 | 10.20.5.1-10.20.5.126 | 10.20.5.127 | 126 |
| management | 10.20.5.128/27 | 10.20.5.129-10.20.5.158 | 10.20.5.159 | 30 |

Validate the NACL with the provided simulator:

```bash
make nacl
# Correct: 6 / 6 = 100.0%
```

## Task 6 - reproducibility, tests, CI/CD

```bash
make install               # lightweight dev/test environment
make lint test verify      # ruff + pytest + NACL
make secret-scan           # gitleaks (install the binary first)
```

CI (`.github/workflows/ci.yml`) runs, in order: lint -> unit tests -> model
evaluation from the GitHub Release -> quality gate `--min-validity 95`, plus
gitleaks. For the required failing demonstration:

1. Open **Actions -> ci -> Run workflow**.
2. Set `bad_model = true` and run. The `quality-gate-demo` job evaluates the
   untrained `EleutherAI/pythia-70m` and fails at the gate.
3. Put the URL of that failed run in `report/report.md` (section Task 6).

## Task 7 - performance, cost, SLO

```bash
make loadtest              # standard Locust command, 4 users, 2 minutes
make cost                  # reads results/loadtest_stats.csv
```

Locust writes `results/loadtest_stats.csv` and
`results/loadtest_headers.json`. `make cost` computes:

```
hourly = replicas * (cpu_limit * vcpu_hour + memory_gib * gib_memory_hour)
cost_per_1000 = hourly / requests_per_hour * 1000
```

using `data/rate_card.json`, and also reports the SLO error budget for one
load-test window. Cluster resource usage is captured with `kubectl top pods`.

## Evidence and status

- `results/` contains commands and outputs that were actually produced,
  including teacher-labeling, training/evaluation, CI, and live Kubernetes
  evidence.
- `report/final_audit.md` maps every marking criterion to its implementation
  and status.
- The live k3d deployment passed health/readiness/parse checks through a
  Traefik port-forward. The recorded Ingress load test completed 76 requests
  with zero HTTP failures; three cold starts took 11, 11, and 10 seconds, and
  pod recovery took 12 seconds. The short CPU-bound load test did not establish
  balanced per-replica traffic.
- SeaweedFS refused the v2 archive upload with `no free volumes left`.
  The published `final` release contains the v1 model archive and checksum;
  the archive includes its tokenizer, configuration, training metadata, and
  checksums. The v1-only rolling update verifies Kubernetes behavior, not a
  v1-to-v2 artifact switch.

Build the project report with `make report`; Pandoc and XeLaTeX must be
installed. The generated `report/report.pdf` is checked to remain within the
eight-page limit.

## Release and final tag

The repository's `final` GitHub Release contains `model.tar.gz` and
`model.sha256`. The model archive includes the tokenizer and version/training
metadata. The report documents the remaining v2 object-storage limitation.
Before submission, verify the `final` tag points to the exact complete source
commit and retain the existing Release assets. Do not add model weights to Git;
publish them as GitHub Release assets.
