#!/usr/bin/env bash
# Start the API (:8000) and the Vite UI (:5173); Ctrl-C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONPATH=backend:.
export PYTHONWARNINGS=ignore::DeprecationWarning
export MODEL_ADAPTER="${ADAPTER:-${MODEL_ADAPTER:-mock}}"
export ESCALATION_TIMEOUT_SEC="${ESCALATION_TIMEOUT_SEC:-30}"
PY=.venv/bin/python

echo "API http://127.0.0.1:8000   UI http://127.0.0.1:5173   adapter=${MODEL_ADAPTER}   escalation=${ESCALATION_TIMEOUT_SEC}s"
if [[ "${MODEL_ADAPTER}" == "ollama" ]] && ! curl -s -m 2 http://localhost:11434/api/tags >/dev/null; then
  echo "WARNING: Ollama is not reachable on :11434 — start it (ollama serve) or use ADAPTER=mock"
fi

trap 'kill 0' EXIT INT TERM
$PY -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 ${RELOAD:+--reload} &
(cd web && npm run dev -- --host 127.0.0.1 --strictPort) &
wait
