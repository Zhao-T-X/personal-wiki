import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Isolate the test session from the developer's real database and settings
# before any app module binds them at import time. Without this, app.config
# reads data/settings.json (which pins ./data/wiki.db) and the tests end up
# writing to the real knowledge base.
_TMP = Path(tempfile.mkdtemp(prefix='llmwiki-tests-'))
os.environ.setdefault('DATABASE_PATH', str(_TMP / 'wiki.db'))
os.environ.setdefault('SETTINGS_PATH', str(_TMP / 'settings.json'))


@pytest.fixture(autouse=True)
def _llm_test_mode(request):
    """Make ``pytest`` cost 0 tokens by default.

    Every test runs with ``LLM_TEST_MODE=disabled`` so a real LLM call raises instead of
    silently spending money; a test that genuinely needs the model opts in with
    ``@pytest.mark.live_llm`` (deselected by default — see pytest.ini).
    """
    from app import config
    live = request.node.get_closest_marker('live_llm') is not None
    had = 'llm_test_mode' in config._OVERRIDES
    prev = config._OVERRIDES.get('llm_test_mode')
    config._OVERRIDES['llm_test_mode'] = 'live' if live else 'disabled'
    try:
        yield
    finally:
        if had:
            config._OVERRIDES['llm_test_mode'] = prev
        else:
            config._OVERRIDES.pop('llm_test_mode', None)
