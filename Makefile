install:
	python -m pip install -r requirements.txt

db:
	python scripts/init_db.py

test:
	pytest -q

run:
	uvicorn app.main:app --host 127.0.0.1 --port 8000

export:
	python scripts/export_json.py
