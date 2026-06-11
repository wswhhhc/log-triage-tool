#!/bin/bash
cd "$(dirname "$0")/.."
rm -f anomaly_triage.db
echo "http://localhost:8000"
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
