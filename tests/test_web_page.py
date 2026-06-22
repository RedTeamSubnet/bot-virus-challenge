"""Tests for the /_web challenge page and the browser SDK.

Two layers:
  * Python: the /_web route serves the minimal page with the elements the
    automation depends on, and the SDK assets are served by the static mount.
  * Node: the SDK behaviour (writing localStorage["data"]) is proven by the
    standalone JS suites under tests/web/, shelled out here so a single
    `pytest` run covers the whole browser-side flow. Skipped if node is absent.
"""

import shutil
import pathlib
import subprocess

import pytest
from fastapi.testclient import TestClient

from src.bv_challenge.challenge.api.main import app

client = TestClient(app)

_WEB_DIR = pathlib.Path(__file__).resolve().parents[1] / "tests" / "web"


def test_web_page_loads() -> None:
    _response = client.get("/_web")
    assert _response.status_code == 200
    assert "text/html" in _response.headers["content-type"]


def test_web_page_has_required_elements() -> None:
    _html = client.get("/_web").text
    # Interactive elements the bot (and a human) act on.
    assert 'name="username"' in _html
    assert 'name="password"' in _html
    assert 'id="login-button"' in _html
    assert 'class="end-session"' in _html
    # Tall content => the page is scrollable to reach the end button.
    assert 'id="content"' in _html


def test_web_page_injects_backend_context_and_sdk() -> None:
    _html = client.get("/_web").text
    # Globals the automation reads.
    assert "window.APP_ID" in _html
    assert "window.PUBLIC_KEY" in _html
    assert "window.ACTIONS_LIST" in _html
    # SDK is wired in (collector before sdk).
    assert "/static/js/collector.js" in _html
    assert "/static/js/sdk.js" in _html


def test_web_page_exposes_task_config() -> None:
    _html = client.get("/_web").text
    # Machine-readable task contract for the automation (stable selectors only).
    assert "window.TASK_CONFIG" in _html
    for _key in (
        "usernameInput",
        "passwordInput",
        "verifyButton",
        "scrollContainer",
        "endSessionButton",
        "payloadKey",
    ):
        assert _key in _html, _key


def test_web_page_does_not_leak_scoring_internals() -> None:
    _html = client.get("/_web").text.lower()
    # The public page must not reference the private detector / gate / thresholds,
    # nor any of the advanced scoring internals (weights/penalties/sub-scores).
    for _needle in (
        "rt_bv_score",
        "metricsprocessor",
        "passes_gate",
        "threshold",
        "score_message",
        "self_consistency",
        "weighted_average",
        "gate_fail",
        "penalty",
    ):
        assert _needle not in _html


def test_sdk_assets_do_not_leak_scoring_internals() -> None:
    # The collected raw signals are public, but no scoring math/thresholds may
    # appear in the shipped JS either.
    for _path in ("/static/js/collector.js", "/static/js/sdk.js"):
        _js = client.get(_path).text.lower()
        for _needle in (
            "rt_bv_score",
            "metricsprocessor",
            "threshold",
            "weight",
            "penalty",
            "score_message",
            "self_consistency",
        ):
            assert _needle not in _js, f"{_needle} leaked into {_path}"


def test_sdk_assets_are_served() -> None:
    for _path in ("/static/js/collector.js", "/static/js/sdk.js"):
        _response = client.get(_path)
        assert _response.status_code == 200, _path
        assert "javascript" in _response.headers["content-type"].lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not available")
@pytest.mark.parametrize("script", ["collector.test.js", "sdk.test.js"])
def test_sdk_node_suites(script: str) -> None:
    _result = subprocess.run(
        ["node", str(_WEB_DIR / script)],
        capture_output=True,
        text=True,
    )
    assert _result.returncode == 0, _result.stdout + _result.stderr
