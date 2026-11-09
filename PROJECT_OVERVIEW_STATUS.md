# Cloud Computing Project Overview and Submission Status

## Project title

**Knowledge Distillation and Deployment of a Lightweight Command-Parsing Language Model**

## Project purpose

This project builds and deploys a small language model that converts natural-language
cloud commands into structured JSON. A larger teacher model labels the training
commands, a smaller student model learns from those labels, and the student is
served through a FastAPI application running on Kubernetes.

The project is designed to demonstrate:

- Object storage and parallel data processing.
- Knowledge distillation and CPU-based model training.
- REST API design and model serving.
- Docker containerization.
- Kubernetes deployment, health probes, ingress, and autoscaling.
- Network subnet and NACL design.
- CI/CD, testing, performance measurement, and cost analysis.

## Architecture

```text
3,000 command records
        |
        v
Ollama phi3.5 teacher model
        |
        v
Four resumable labeling workers
        |
        v
SeaweedFS/object storage: teacher shards
        |
        v
worker/clean.py
        |
        v
Cleaned training dataset + checksum
        |
        v
Pythia-70M student training (v1 and v2)
        |
        v
Model archives in object storage
        |
        v
FastAPI command parser
        |
        v
Docker image and Kubernetes Deployment
        |
        v
Service + Ingress + HPA + Locust performance testing
```

## Repository structure

| Location | Purpose |
|---|---|
| `data/` | Assignment command pool, validation data, schemas, and rate card |
| `worker/` | Teacher labeling worker, shard handling, storage abstraction, and cleaning |
| `training/` | Student model training, prediction, configurations, and loss files |
| `service/` | FastAPI application, model loading, validation, and Dockerfile |
| `k8s/` | Namespace, SeaweedFS, worker Job, application, ingress, and HPA manifests |
| `cluster/` | k3d cluster configuration |
| `network/` | Subnet allocation and NACL rules |
| `scripts/` | Setup, storage, labeling, evaluation, benchmarking, and deployment scripts |
| `tests/` | Unit and service tests |
| `tools/` | Scoring, NACL simulation, and Locust load-test tools |
| `results/` | Reproducibility, test, checksum, benchmark, and validation evidence |
| `report/` | Final report source, PDF, and audit |
| `.github/workflows/ci.yml` | Lint, tests, secret scan, model evaluation, and quality gate |

## Work completed

### Application implementation

- Implemented four-way sharded teacher labeling.
- Added resumable workers that skip already completed command IDs.
- Added local and S3-compatible storage backends.
- Added data cleaning for malformed JSON, invalid intents, invalid slots,
  duplicates, and missing records.
- Added checksum and record-count verification.
- Added v1 and v2 CPU training configurations.
- Added FastAPI endpoints:
  - `/parse`
  - `/healthz`
  - `/readyz`
- Added request validation, model-version headers, readiness behavior, and
  invalid-model-output handling.
- Added Docker configuration with model weights loaded from object storage.
- Added Kubernetes Deployment, Service, Ingress, probes, resource bounds, and HPA.
- Added SeaweedFS manifests and storage initialization scripts.
- Added subnet and NACL configuration.
- Added CI workflow and an intentionally failing quality-gate demonstration.
- Added Locust load testing, local benchmarking, cost calculation, and runbook
  documentation.

### Locally verified

- Ruff lint: **passed**.
- Unit tests: **49 passed**.
- NACL simulator: **6/6 packets correct**.
- Python compilation: **passed**.
- Kubernetes YAML parsing: **8 files / 15 documents parsed**.
- Report PDF: **successfully built**.
- Local v1 and v2 smoke model archives: **created with checksums**.
- Local FastAPI inference benchmark: **completed**.
- Secret-scan configuration and local evidence: **present**.

### Live Kubernetes validation (2026-11-08)

- Docker Engine: **29.1.3**, reachable.
- k3d cluster `kd-cloud`: **1 server and 1 agent, both Ready**.
- SeaweedFS: **Running and Ready**, with the `datasets` and `models` buckets
  reachable through a `kubectl port-forward`.
- v1 model archive: **uploaded to live object storage** and loaded by the
  application pods.
- Application Deployment: **2/2 replicas Ready** on separate k3d nodes.
- Service endpoints: **2 application endpoints registered**.
- Traefik Ingress: `/healthz`, `/readyz`, and `/parse` each returned **HTTP
  200** through a Kubernetes port-forward.
- Live inference: **10/10 requests succeeded** with HTTP 200.
- Metrics-server: **available**; observed pod memory was approximately
  **395–398 MiB** and HPA reported **38% CPU / 70% target** with two desired
  replicas.
- Rolling update and rollback: **passed** using the working v1 artifact;
  pod recovery completed in **12 seconds**.
- Cold starts: **11 seconds, 11 seconds, and 10 seconds**.
- Ingress load test: **76 requests, 0 failures**, p95 **7.2 seconds**.
- HPA scale-down to one replica and subsequent scale-up were observed.

The host-side k3d mappings on ports 8080 and 8333 returned empty responses, so
the tests used supported `kubectl port-forward` paths. The remaining runtime
evidence gap is balanced traffic distribution: the short, CPU-bound test
served all successful requests from one replica. The v2 upload remains blocked
by SeaweedFS reporting `no free volumes left`.

## Current teacher-labeling status

The full assignment-compliant `phi3.5` labeling run is complete. All four
shards finished successfully:

| Shard | Completed | Target |
|---|---:|---:|
| Shard 0 | 750 | 750 |
| Shard 1 | 750 | 750 |
| Shard 2 | 750 | 750 |
| Shard 3 | 750 | 750 |
| **Total** | **3,000** | **3,000** |

Teacher labeling completion is **100%**. The full local cleaning run also
completed successfully:

- Input records: **3,000**
- Kept records: **2,782**
- Invalid JSON: **49**
- Wrong keys: **4**
- Invalid slots: **118**
- Duplicate commands: **47**
- Invalid intents: **0**
- Cleaned dataset SHA-256:
  `7e286136aaaaf4fd15e86ad4fe3c449b304182a69f7dd2664a0d729f13d33734`

## Completion estimate

These are engineering estimates based on implementation and verified evidence,
not official marks.

| Area | Estimated completion |
|---|---:|
| Project implementation and source structure | 100% |
| Teacher labeling and full dataset evidence | 100% |
| Student model training | 100% |
| Model evaluation | 100% |
| FastAPI service | 100% |
| Docker image validation | 80% |
| Kubernetes deployment validation | 90% |
| Networking and NACL | 100% |
| CI/CD | 100% |
| Performance and cost evidence | 75% |
| Final packaging and publication | 80% |
| **Overall submission readiness** | **Approximately 93%** |

Approximately **7% remains** before the project can honestly be described as
fully submission-ready. The source implementation is much further along than
the runtime evidence.

## Remaining work

### Remaining submission items

- Retry the v2 SeaweedFS upload after restoring its free volume capacity.
- Run a longer load test that demonstrates traffic to both replicas.
- Run the separate Docker benchmark with 1 CPU and 1 GiB limits.
- Handwrite the reflection, scan it as `230101050.pdf` (maximum three pages),
  and include it with `report/report.pdf` and the source tree in
  `230101050.zip`.
- Ensure the `final` Git tag points to the exact complete source commit.

## Important status conclusion

The project implementation, full training/evaluation, release assets, CI, and
most live Kubernetes checks are complete. Remaining items are the v2 storage
capacity issue, more representative load-balancing and container-resource
measurements, the student's handwritten scan, and the final tag/package check.

For detailed audit information, see:

- [README.md](README.md)
- [PROJECT_STATUS.md](PROJECT_STATUS.md) (historical execution notes)
- [report/final_audit.md](report/final_audit.md)
- [docs/RUNBOOK.md](docs/RUNBOOK.md)
