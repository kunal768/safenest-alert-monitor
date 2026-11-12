PYTHON ?= python3.11
VENV := .venv
BIN := $(VENV)/bin
export PYTHONPATH := backend:.
ADAPTER ?= mock
ESCALATION_TIMEOUT_SEC ?= 30

.PHONY: setup seed run test test-ollama demo clips cloud-check build

setup:
	test -x $(BIN)/python || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install -r backend/requirements.txt
	$(BIN)/python simulator/make_clips.py
	cd web && npm install

seed:
	$(BIN)/python -m app.seed

run:
	ADAPTER=$(ADAPTER) ESCALATION_TIMEOUT_SEC=$(ESCALATION_TIMEOUT_SEC) ./scripts/run.sh

test:
	MODEL_ADAPTER=mock $(BIN)/python -m pytest tests -v -m "not ollama" --cov=backend/app --cov=ai --cov-report=term

test-ollama:
	MODEL_ADAPTER=ollama $(BIN)/python -m pytest tests -v -m ollama

demo: clips seed run

build:
	cd web && npm run build

cloud-check:
	$(BIN)/python deploy/cloud_check.py

clips:
	$(BIN)/python simulator/make_clips.py

