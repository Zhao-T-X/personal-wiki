@echo off
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt
if not exist .env copy .env.example .env
python scripts\init_db.py
uvicorn app.main:app --host 127.0.0.1 --port 8000
