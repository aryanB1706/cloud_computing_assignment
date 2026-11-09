# Task 4 runbook: scaling, rollout, recovery, cold start, load balancing

Run everything from the repository root. The teacher (if used) must be started
with `OLLAMA_HOST=0.0.0.0:11434 ollama serve`; otherwise pods cannot reach
`host.k3d.internal:11434`. Keep a second terminal running the standard load
test for the whole experiment:

```bash
make loadtest LOCUST_RUNTIME=8m > results/loadtest_cluster.txt 2>&1 &
```

All evidence below is written to `results/`. Record the **actual** timings and
numbers; do not estimate.

## 0. Baseline

```bash
kubectl -n app get deploy,svc,ingress,hpa,pods -o wide | tee results/cluster_k8s_objects.txt
kubectl -n app rollout status deploy/command-parser
kubectl -n app get pods -l app=command-parser -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}'
```

Check that SeaweedFS is reachable and that each pod logged a download:

```bash
kubectl -n app logs deploy/command-parser | grep -iE 'download|checksum|model loaded' \
  | tee results/cluster_coldstart_logs.txt
```

## 1. HPA: scale 1 -> 2 -> 1

```bash
kubectl -n app get hpa command-parser -w | tee results/cluster_hpa.txt &
# with the load test running, wait for TARGETS to exceed 70% / REPLICAS 2
kubectl -n app get pods -l app=command-parser -w
# then stop the load and wait for the 60 s scale-down window
```

Record the time of the last `Successful rescale` event:

```bash
kubectl -n app describe hpa command-parser | sed -n '/Events/,$p'
```

Local limitation to state in the report: the cluster is a single k3d node on
one laptop; both replicas share the same CPU, so the HPA measures contention,
not machine-level fault tolerance.

## 2. Rolling update v1 -> v2, then rollback

```bash
kubectl -n app set env deploy/command-parser \
  MODEL_URI=s3://models/v2/model.tar.gz MODEL_VERSION=v2
kubectl -n app rollout status deploy/command-parser | tee results/cluster_rolling_update.txt
kubectl -n app get pods -l app=command-parser -o wide

# /parse must now report v2; sample a few requests
for i in $(seq 1 5); do
  curl -s -D - -o /dev/null -X POST http://localhost:8080/parse \
    -H 'Content-Type: application/json' \
    -d '{"text":"what is the weather in london"}' | grep -i 'x-model-version'
done

kubectl -n app rollout undo deploy/command-parser
kubectl -n app rollout status deploy/command-parser
# verify v1 again
```

The Locust summary gives the failed-request percentage for the update window;
the target is <= 0.5%. `maxUnavailable: 0` and readiness probes keep traffic on
old pods until new pods are Ready.

## 3. Recovery: delete a pod

```bash
kubectl -n app get pods -l app=command-parser
kubectl -n app delete pod <pod-name>
kubectl -n app get pods -l app=command-parser -w | tee results/cluster_recovery.txt
```

Record the time until the replacement is `1/1 Running` and confirm the Locust
failure rate stayed <= 2%.

## 4. Cold start x3

For each run, delete all pods and time from creation to Ready:

```bash
for run in 1 2 3; do
  kubectl -n app delete pods -l app=command-parser --wait=true
  start=$(date +%s)
  kubectl -n app wait --for=condition=Ready pod -l app=command-parser --timeout=300s
  end=$(date +%s)
  echo "run $run: $((end-start)) s" | tee -a results/cluster_coldstart.txt
done
```

Capture download log lines to `results/cluster_coldstart_logs.txt`. Report the
median of the three times.

## 5. Load balancing with exactly two Ready replicas

```bash
kubectl -n app scale deploy/command-parser --replicas=2
kubectl -n app rollout status deploy/command-parser
make loadtest LOCUST_RUNTIME=2m
cat results/loadtest_headers.json | tee results/cluster_loadtest_headers.json
```

Each pod must appear in `per_replica` and serve at least 30% of successful
requests. The standard command uses the Ingress at `http://localhost:8080`.

## 6. Resource usage during the test

```bash
kubectl -n app top pods | tee results/cluster_top_pods.txt
```

Use the numbers in Task 7 and in the review table.

## 7. Readiness during model download

To demonstrate that the Service has no ready endpoints while a pod loads:

```bash
kubectl -n app run debug --rm -it --image=curlimages/curl --restart=Never -- \
  sh -c 'curl -s -o /dev/null -w "%{http_code}\n" http://command-parser.app.svc.cluster.local/readyz'
# 503 while the new pod is still downloading, then 200
```
