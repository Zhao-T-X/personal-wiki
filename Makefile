install:
	python -m pip install -r requirements.txt

db:
	python scripts/init_db.py

test:
	pytest -q

# Architecture boundary guard + evaluation regression baselines.
test-arch:
	pytest tests/test_architecture.py tests/test_eval_regression.py -q

# Regenerate the evaluation baseline metrics from the golden sets (offline).
eval-baseline:
	python -m app.evaluation.baseline

run:
	uvicorn app.main:app --host 127.0.0.1 --port 8000

export:
	python scripts/export_json.py
