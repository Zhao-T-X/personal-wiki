#!/usr/bin/env bash
set -euo pipefail
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; fi
python scripts/init_db.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
