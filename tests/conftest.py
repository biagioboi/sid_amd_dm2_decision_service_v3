import os
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def use_sqlite_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Force SQLite for unit tests.
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("DEV_USERNAME", "admin")
    monkeypatch.setenv("DEV_PASSWORD", "admin")
    monkeypatch.setenv("JWT_SECRET", "test-secret")
    # Re-import persistence to pick up env.
    import importlib
    import app.persistence as persistence

    importlib.reload(persistence)
    persistence.init_db()
    yield
