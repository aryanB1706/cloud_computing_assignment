# Current Project Status

Status date: 2026-11-09
Repository: https://github.com/aryanB1706/cloud_computing_assignment
Student: Aryan Kumar, roll number 230101050

This file supersedes the earlier in-progress execution notes. The detailed
overview is in `PROJECT_OVERVIEW_STATUS.md`; rubric evidence is in
`report/final_audit.md`.

## Completed and measured

- Teacher labeling processed all 3,000 commands; cleaning retained 2,782
  records and the accounting invariant passed.
- Student v1 and v2 were each trained on 1,500 examples and evaluated.
- The final GitHub Release contains the v1 model archive and SHA-256 checksum.
  The archive contains weights, tokenizer, config, training metadata, and
  per-file checksums. Model weights are ignored by Git.
- FastAPI, Docker image build (1.63 GB), object-storage model loading, and
  Kubernetes deployment were exercised.
- Both k3d nodes reached Ready. Ingress health/readiness/parse requests passed.
- The short Ingress load test completed 76 requests with zero HTTP failures:
  p50 6.0 seconds, p95 6.6 seconds, 1.297 requests/second.
- The v1-only rolling update and rollback passed. Pod recovery was 12 seconds;
  three cold starts were 11, 11, and 10 seconds.
- HPA scale-up and scale-down events were observed. NACL simulation passed
  6/6 cases; 49 unit tests passed; the normal CI workflow and expected-failure
  quality-gate demonstration were recorded.

## Limitations still to disclose

- SeaweedFS reported `no free volumes left` during the v2 model upload.
  Therefore the v1-only deployment test does not prove a real v1-to-v2
  artifact change.
- All successful requests in the short Locust test were served by one replica;
  balanced per-replica traffic is not demonstrated.
- The separate Docker run with 1 CPU and 1 GiB memory was not measured.
- The Ingress p95 of 6.6 seconds exceeds a 5-second target if that target is
  applied to this test. The run is short and not a sustained capacity test.
- The handwritten reflection scan and final ZIP are student-side deliverables.

## Submission files

- Main report: `report/report.pdf` (maximum 8 pages; verify after build).
- Handwritten reflection: write the draft in `docs/reflection_notes.md` by
  hand and scan as `230101050.pdf` (maximum 3 pages).
- ZIP for upload: `230101050.zip`, containing the report, handwritten scan,
  source tree, README, test/evaluation scripts, manifests, network files, CI,
  and `results/`. Do not include ignored model weights in Git; use the Release.
- Verify that the GitHub tag `final` points to the complete submission commit.
