SHELL := /bin/bash
PYTHON ?= python3
VENV ?= .venv
PY := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
SUBSET ?=
CONFIG ?= training/config.yaml
MODEL_VERSION ?= v1
CLUSTER ?= kd-cloud
SERVICE_IMAGE ?= command-parser-service:0.1.0
WORKER_IMAGE ?= command-parser-worker:0.1.0
LOCUST_USERS ?= 4
LOCUST_RUNTIME ?= 2m
HOST ?= http://localhost:8080

.PHONY: help venv install install-ml image worker-image lint test verify \
        up down storage-init fetch-model seed-models train train-v2 \
        teacher-label clean evaluate loadtest nacl cost report secret-scan \
        bench-local

help:
	@echo "Knowledge-distillation command parser"
	@echo ""
	@echo "Setup:      make install            (dev/test dependencies)"
	@echo "            make install-ml         (CPU PyTorch + model deps)"
	@echo "Cluster:    make up                 (create cluster, storage, images, deploy)"
	@echo "            make down               (delete the cluster)"
	@echo "Storage:    make storage-init       (create buckets, upload kit data)"
	@echo "            make fetch-model        (download Release asset into models/v1)"
	@echo "            make seed-models        (upload models/ to SeaweedFS)"
	@echo "Pipeline:   make teacher-label      (parallel Ollama labelling Job)"
	@echo "            make clean              (validate shards -> train.jsonl)"
	@echo "Training:   make train              (v1, reads training/config.yaml)"
	@echo "            make train SUBSET=50    (training smoke test)"
	@echo "            make train-v2           (v2, reads training/config_v2.yaml)"
	@echo "            make evaluate           (teacher/base/student on validation)"
	@echo "Quality:    make lint test verify secret-scan"
	@echo "Perf:       make loadtest           (Locust standard command)"
	@echo "            make bench-local        (50 sequential requests, local)"
	@echo "            make cost               (cost per 1000 requests)"
	@echo "            make nacl               (official NACL simulator)"
	@echo "Report:     make report             (build report/report.pdf if TeX is present)"

venv:
	@test -x $(PY) || $(PYTHON) -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip==24.0
	$(PIP) install -r requirements-dev.txt

install-ml: venv
	$(PIP) install --upgrade pip==24.0
	$(PIP) install torch==2.4.1 --index-url https://download.pytorch.org/whl/cpu
	$(PIP) install -r training/requirements.txt -r requirements-dev.txt

image:
	docker build -f service/Dockerfile -t $(SERVICE_IMAGE) .

worker-image:
	docker build -f worker/Dockerfile -t $(WORKER_IMAGE) .

lint:
	$(VENV)/bin/ruff check .

test:
	$(VENV)/bin/pytest -q

verify: lint test nacl

up:
	@test -x $(PY) || $(MAKE) install-ml
	bash scripts/up.sh

down:
	bash scripts/down.sh

storage-init:
	bash scripts/storage_init.sh

fetch-model:
	bash scripts/fetch_model.sh

seed-models:
	bash scripts/seed_models.sh

teacher-label:
	kubectl delete job -n pipeline teacher-labelling --ignore-not-found
	kubectl apply -f k8s/worker-job.yaml

clean:
	$(PY) worker/clean.py

train:
	$(PY) training/train.py --config $(CONFIG) $(if $(SUBSET),--subset $(SUBSET),)

train-v2:
	$(PY) training/train.py --config training/config_v2.yaml $(if $(SUBSET),--subset $(SUBSET),)

evaluate:
	bash scripts/evaluate.sh

loadtest:
	@mkdir -p results
	$(VENV)/bin/locust -f tools/locustfile.py --headless --users $(LOCUST_USERS) \
		--spawn-rate $(LOCUST_USERS) --run-time $(LOCUST_RUNTIME) \
		--host $(HOST) --csv results/loadtest

nacl:
	python3 tools/nacl_sim.py network/nacl.json network/packets_sample.jsonl --check

cost:
	$(PY) scripts/cost.py --csv results/loadtest_stats.csv

report:
	bash scripts/build_report.sh

secret-scan:
	gitleaks detect --source . --config .gitleaks.toml --redact --no-banner --no-git

bench-local:
	$(PY) scripts/bench_local.py
