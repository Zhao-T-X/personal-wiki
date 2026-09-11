from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.db import init_db
init_db()
print('Initialized SQLite database.')
