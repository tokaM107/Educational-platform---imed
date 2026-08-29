"""The local demo UI is opt-in and absent from production routing."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.main import register_demo_ui


def settings(enabled, static_dir):
    return SimpleNamespace(enable_demo_ui=enabled, static_dir=static_dir)


def test_demo_ui_is_disabled_by_default(tmp_path):
    application = FastAPI()

    assert register_demo_ui(application, settings(False, tmp_path)) is False

    client = TestClient(application)
    assert client.get("/").status_code == 404
    assert client.get("/static/app.js").status_code == 404


def test_demo_ui_can_be_enabled_explicitly(tmp_path):
    (tmp_path / "index.html").write_text("demo home", encoding="utf-8")
    (tmp_path / "app.js").write_text("demo script", encoding="utf-8")
    application = FastAPI()

    assert register_demo_ui(application, settings(True, tmp_path)) is True

    client = TestClient(application)
    assert client.get("/").text == "demo home"
    assert client.get("/static/app.js").text == "demo script"


def test_missing_demo_directory_never_breaks_startup(tmp_path):
    application = FastAPI()
    missing = tmp_path / "not-copied-into-production"

    assert register_demo_ui(application, settings(True, missing)) is False

    client = TestClient(application)
    assert client.get("/").status_code == 404
    assert client.get("/static/app.js").status_code == 404
