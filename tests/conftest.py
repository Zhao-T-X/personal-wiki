import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Isolate the test session from the developer's real database and settings
# before any app module binds them at import time. Without this, app.config
# reads data/settings.json (which pins ./data/wiki.db) and the tests end up
# writing to the real knowledge base.
_TMP = Path(tempfile.mkdtemp(prefix='llmwiki-tests-'))
os.environ.setdefault('DATABASE_PATH', str(_TMP / 'wiki.db'))
os.environ.setdefault('SETTINGS_PATH', str(_TMP / 'settings.json'))
