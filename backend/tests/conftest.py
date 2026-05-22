import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

BOOTSTRAP_WIKI_DIR = BACKEND_DIR / ".test-wiki-bootstrap"
BOOTSTRAP_WIKI_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("WIKI_REPO_PATH", str(BOOTSTRAP_WIKI_DIR))


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app import config
    from app import db as db_module
    from app.main import app
    from app.routes import chat

    wiki_repo = tmp_path / "wiki-repo"
    raw_dir = wiki_repo / "raw"
    data_dir = tmp_path / "data"
    db_path = data_dir / "app.db"

    for category in config.CATEGORIES:
        (raw_dir / category).mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(config, "WIKI_REPO_DIR", wiki_repo)
    monkeypatch.setattr(config, "RAW_DIR", raw_dir)
    monkeypatch.setattr(config, "DATA_DIR", data_dir)
    monkeypatch.setattr(config, "DB_PATH", db_path)
    monkeypatch.setattr(db_module, "DB_PATH", db_path)
    monkeypatch.setattr(chat, "DB_PATH", db_path)
    chat._pending_permissions.clear()

    with TestClient(app) as test_client:
        test_client.db_path = db_path
        test_client.raw_dir = raw_dir
        test_client.wiki_repo = wiki_repo
        yield test_client

    chat._pending_permissions.clear()
