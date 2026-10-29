"""The /_web challenge page: template context and served assets."""

import json
import pathlib
import re

from fastapi.testclient import TestClient

from src.bv_challenge.challenge.api.main import app
from api.endpoints.challenge import payload_manager as pm_mod
from api.endpoints.challenge import service
from api.endpoints.challenge.payload_manager import PayloadManager
from api.endpoints.challenge.schemas import KeyPairPM

client = TestClient(app)

_HTML_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src"
    / "bv_challenge"
    / "challenge"
    / "templates"
    / "html"
)


def _fake_key_pairs() -> list[KeyPairPM]:
    return [
        KeyPairPM(
            private_key=f"private-key-{i}".ljust(40, "x"),
            public_key=f"public-key-{i}".ljust(40, "x"),
            nonce=f"nonce{i}abcdef",
        )
        for i in range(3)
    ]


def test_web_page_loads() -> None:
    response = client.get("/_web")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_web_page_exposes_config_and_session_globals() -> None:
    html = client.get("/_web").text
    assert "window.TASK_CONFIG" in html
    assert '"/_eval"' in html
    assert "window.PUBLIC_KEY" in html
    assert "window.BV_SESSION" in html
    for key in ("sessionId", "nonce", "publicKeyId", "configHash", "schemaVersion"):
        assert key in html, key


def test_web_page_session_schema_version_is_3() -> None:
    html = client.get("/_web").text
    assert re.search(r'schemaVersion:\s*"3"', html)


def test_web_page_does_not_leak_scoring_internals() -> None:
    html = client.get("/_web").text.lower()
    for needle in ("rt_bv_score", "metricsprocessor", "score_message", "gate_fail"):
        assert needle not in html


def test_built_assets_are_served() -> None:
    manifest = json.loads((_HTML_DIR / "asset-manifest.json").read_text())
    for name in ("main.js", "main.css"):
        path = manifest["files"][name]
        response = client.get(path)
        assert response.status_code == 200, path


def test_get_web_passes_task_and_stores_expected_answer(monkeypatch, tmp_path) -> None:
    # Own template so the test does not depend on the built page.
    html_dir = tmp_path / "templates" / "html"
    html_dir.mkdir(parents=True)
    (html_dir / "index.html").write_text(
        '<script>window.N="{{ nonce }}";window.BV_TASK = {{ task | tojson }};</script>'
    )
    monkeypatch.setattr(service, "_SRC_DIR", tmp_path)
    monkeypatch.setattr(pm_mod.challenge_utils, "gen_key_pairs", _fake_key_pairs)
    manager = PayloadManager()
    manager.pop_task()
    monkeypatch.setattr(service, "payload_manager", manager)

    html = client.get("/_web").text

    nonce = re.search(r'window\.N="([^"]+)"', html).group(1)
    task = json.loads(re.search(r"window\.BV_TASK = (\{.*?\});</script>", html).group(1))
    assert set(task) == {
        "seed",
        "code",
        "cardCount",
        "codeCardIndex",
        "sliderTarget",
        "sliderTolerance",
        "ids",
    }
    assert manager.tasks[nonce] == task
    assert nonce in manager.sessions

    # Each page load gets its own task bound to its own nonce.
    html2 = client.get("/_web").text
    nonce2 = re.search(r'window\.N="([^"]+)"', html2).group(1)
    assert nonce2 != nonce
    assert set(manager.tasks) == {nonce, nonce2}
