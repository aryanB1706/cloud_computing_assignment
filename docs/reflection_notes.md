# Handwritten Reflection Draft

Copy this reflection by hand, then scan it as `230101050.pdf`. Keep the scan
within three pages. This typed draft is content guidance, not the required
handwritten scan.

**Name:** Aryan Kumar<br>
**Roll number:** 230101050<br>
**Course:** CS3081 Cloud Computing<br>
**GitHub repository:** https://github.com/aryanB1706/cloud_computing_assignment

## Reflection

I built a command-parsing service using knowledge distillation and local-cloud
tools. A `phi3.5` teacher labelled natural-language commands with JSON. I
cleaned the labels, trained `EleutherAI/pythia-70m` students, and served a
student through FastAPI and Kubernetes. This project connected data
processing, object storage, containerization, networking, testing, and
deployment.

I learned that sequence-level distillation works when a teacher API returns
generated text but not token logits: the teacher's text becomes the student's
training target. I trained v1 and v2 on 1,500 examples each. On 100 evaluation
examples, v2 achieved 100% JSON validity and 38% full-frame accuracy; the
teacher scored 40% full-frame accuracy. This taught me that valid JSON does not
guarantee correct fields, and that evaluation-set size matters.

The full labeling run processed 3,000 commands. Cleaning retained 2,782:
49 invalid JSON, 4 wrong-key records, 118 invalid slots, 47 duplicates, and
2,782 kept. The accounting invariant and checksums helped verify the results
instead of trusting a command's exit status.

Teacher labeling was slow on CPU, so I used sharding, resumable progress, and
per-record writes. I also learned why Kubernetes readiness differs from
liveness: liveness checks whether a process should keep running, while
readiness prevents traffic before the model is loaded. Live health, readiness,
and parse requests passed through the Ingress.

SeaweedFS later reported `no free volumes left` while I uploaded v2. I did not
claim a real model-version switch; instead, I tested rolling update and
rollback with the working v1 model and documented the blocked upload. The
Ingress test completed 76 requests with no HTTP failures, but traffic was
served by one replica only. Recovery took 12 seconds, and cold starts took 11,
11, and 10 seconds. These are useful measurements, not a sustained capacity
test.

The image was about 1.63 GB, the NACL simulator passed 6/6 cases, and 49 tests
passed. I learned that implementation must be backed by honest measurements:
a short HTTP-success test does not prove semantic accuracy or balanced load.
Next, I would restore SeaweedFS capacity, publish and deploy v2, test both
replicas with a longer load run, and measure Docker at the required CPU and
memory limits. I would also expand the held-out evaluation set. This project
taught me to plan for slow workloads, design for recovery, and state clearly
what each test proves and what remains unknown.
