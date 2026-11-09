# Final Audit Against Submission Criteria

Status date: 2026-11-09. Results below distinguish verified measurements from
limitations and student-side steps still required.

| Task | Status | Evidence | Remaining limitation / improvement |
|---|---|---|---|
| 1. Object storage and teacher labels | Implemented and measured | 3,000 labels, 2,782 clean records, checksum/count invariant, live SeaweedFS and v1 storage | SeaweedFS exhausted free volumes during v2 upload; restore capacity and verify v2 |
| 2. Student training and evaluation | Implemented and measured | v1/v2 trained on 1,500 examples each; evaluation outputs in `results/`; v1 package released with checksum | Expand the 100-example evaluation set; v2 not uploaded to live object storage |
| 3. API and Docker | Implemented and live-tested | FastAPI health/readiness/parse passed; Docker image built at 1.63 GB; Kubernetes loaded model from storage | Separate Docker test constrained to 1 CPU/1 GiB was not measured |
| 4. Kubernetes deployment | Live-tested with limitations | Two Ready replicas on separate nodes; Ingress passed; v1 rollout/rollback passed; recovery 12 s; cold starts 11/11/10 s | Short Locust test did not demonstrate balanced replica distribution; no real v1-to-v2 artifact rollout |
| 5. Networking and NACL | Locally verified | Subnet calculation files; NACL simulator 6/6 correct | Improve with deployment in a target cloud if required |
| 6. Testing and CI/CD | Verified | 49 tests pass; lint passes; normal CI and expected quality-gate failure recorded; secret scan clean | Retain CI links and final tag with submitted source revision |
| 7. Performance, cost, SLO | Measured short run | 76 requests, 0 HTTP failures, p50 6.0 s, p95 6.6 s, 1.297 RPS; estimated 1.69 INR/1,000 successful HTTP responses | p95 exceeds 5 s if that target applies; short run, one replica served the measured traffic, and HTTP success is not semantic correctness |

## Required files and student-side actions

- `README.md` contains the GitHub repository link and build/deploy/test/shutdown
  instructions.
- `report/report.pdf` is the project report and must be no more than eight
  pages (excluding any references/appendices).
- `docs/reflection_notes.md` is a copy-by-hand reflection draft. The student
  must handwrite and scan it as `230101050.pdf`, maximum three pages.
- Assemble `230101050.zip` with the report, handwritten scan, final source
  tree, README, test/training/evaluation code, Dockerfile, Kubernetes and
  network files, CI workflow, and `results/`. Do not commit model weights to
  Git; use the GitHub Release assets.
- Ensure the tag `final` points to the exact complete submission commit.
