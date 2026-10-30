import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("CC_TIMELINE_LANG", raising=False)
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
