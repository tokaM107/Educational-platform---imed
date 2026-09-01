"""The local demo UI is opt-in and absent from production routing."""

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import deps, grading_demo
from app.main import register_demo_ui, register_grading_demo
from app.services import rate_limit


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


def test_grading_demo_api_and_page_are_disabled_by_default(tmp_path):
    application = FastAPI()
    grading_settings = SimpleNamespace(
        enable_grading_demo_ui=False, static_dir=tmp_path
    )

    assert register_grading_demo(application, grading_settings) is False
    client = TestClient(application)
    assert client.get("/grading-demo").status_code == 404
    assert client.get("/api/grading-demo/dataset").status_code == 404


def test_grading_demo_can_be_enabled_explicitly(tmp_path):
    for filename, content in {
        "grading-demo.html": "grading",
        "grading-demo.js": "",
        "grading-demo.css": "",
        "auth.js": "",
        "styles.css": "",
        "login.html": "login",
        "login.js": "",
        "login.css": "",
    }.items():
        (tmp_path / filename).write_text(content, encoding="utf-8")
    application = FastAPI()
    grading_settings = SimpleNamespace(
        enable_grading_demo_ui=True, static_dir=tmp_path
    )

    assert register_grading_demo(application, grading_settings) is True
    application.dependency_overrides[deps.get_conn] = lambda: None
    client = TestClient(application)
    assert client.get("/grading-demo").text == "grading"
    assert client.get("/static/login.html").text == "login"
    assert client.get("/api/grading-demo/dataset").status_code == 401

    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 1, "role": "doctor"
    }
    response = client.get("/api/grading-demo/dataset")
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"

    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 2, "role": "student"
    }
    assert client.get("/api/grading-demo/dataset").status_code == 200


def test_full_dataset_runs_are_cost_limited_per_user(tmp_path, monkeypatch):
    for filename in (
        "grading-demo.html", "grading-demo.js", "grading-demo.css", "auth.js",
        "styles.css", "login.html", "login.js", "login.css",
    ):
        (tmp_path / filename).write_text("asset", encoding="utf-8")

    async def fake_evaluation(service):
        return {"metrics": {}, "cases": []}

    monkeypatch.setattr(grading_demo, "evaluate_dataset", fake_evaluation)
    rate_limit.reset()
    application = FastAPI()
    assert register_grading_demo(
        application,
        SimpleNamespace(enable_grading_demo_ui=True, static_dir=tmp_path),
    ) is True
    application.dependency_overrides[deps.get_current_user] = lambda: {
        "id": 1, "role": "doctor"
    }
    application.dependency_overrides[grading_demo.get_grading_service] = object
    application.dependency_overrides[deps.grading_dataset_llm_quota] = lambda: None
    client = TestClient(application)

    assert client.post("/api/grading-demo/evaluate-dataset").status_code == 200
    assert client.post("/api/grading-demo/evaluate-dataset").status_code == 200
    limited = client.post("/api/grading-demo/evaluate-dataset")
    assert limited.status_code == 429
    assert int(limited.headers["Retry-After"]) > 0
    rate_limit.reset()
