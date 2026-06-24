"""Tests for the /_web challenge page and the browser SDK (HBv6 non-behavioral).

HBv6 scores non-behavioral browser/runtime/session integrity only. The page is
deliberately minimal: it loads the SDK, which collects integrity signals and
submits the encrypted payload to /_eval from the browser. There are no form
fields, no verify button, no scroll content, and no behavioral collection.

Two layers:
  * Python: the /_web route serves the minimal verification page with the
    config/session globals the SDK needs, and the SDK assets are served by the
    static mount.
  * Node: the SDK behaviour (auto-collect + encrypt + submit) is proven by the
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


def test_web_page_is_minimal_verification_page() -> None:
    _html = client.get("/_web").text
    # Minimal status page only.
    assert "Browser verification" in _html
    assert "Checking browser environment" in _html
    assert 'id="status"' in _html


def test_web_page_has_no_form_or_behavioral_elements() -> None:
    _html = client.get("/_web").text
    # No username/password fields, verify button, scroll content, or end button.
    for _needle in (
        'name="username"',
        'name="password"',
        'id="login-button"',
        'id="content"',
        "end-session",
        "ACTIONS_LIST",
    ):
        assert _needle not in _html, _needle


def test_web_page_exposes_minimal_task_config() -> None:
    _html = client.get("/_web").text
    assert "window.TASK_CONFIG" in _html
    # Auto-submit is available and enabled.
    assert '"/_eval"' in _html
    assert "autoSubmit: true" in _html
    assert '"data"' in _html
    # Behavioral selector contract from the old page must be gone.
    for _key in (
        "usernameInput",
        "passwordInput",
        "verifyButton",
        "scrollContainer",
        "endSessionButton",
    ):
        assert _key not in _html, _key


def test_web_page_exposes_session_binding_globals() -> None:
    _html = client.get("/_web").text
    assert "window.BV_SESSION" in _html
    for _key in ("sessionId", "nonce", "publicKeyId", "configHash", "schemaVersion"):
        assert _key in _html, _key
    # Encryption key material the SDK needs for the browser-side submit.
    assert "window.PUBLIC_KEY" in _html


def test_web_page_wires_sdk() -> None:
    _html = client.get("/_web").text
    # SDK is wired in (collector before sdk).
    assert "/static/js/collector.js" in _html
    assert "/static/js/sdk.js" in _html


def test_web_page_does_not_leak_scoring_internals() -> None:
    _html = client.get("/_web").text.lower()
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


def test_sdk_does_not_collect_behavior() -> None:
    # The active flow must not wire mouse/keyboard/scroll/click behavior.
    _js = client.get("/static/js/sdk.js").text
    for _needle in (
        "mousemove",
        "mousedown",
        "mouseup",
        "keydown",
        "keyup",
        "addEventListener",
    ):
        assert _needle not in _js, _needle


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
