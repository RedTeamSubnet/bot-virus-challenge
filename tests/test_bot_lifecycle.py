import threading
import time

import docker
import pytest
import requests

from src.bv_challenge.challenge.api.endpoints.challenge import runner


class RecordingLogger:
    def __init__(self):
        self.records: list[tuple[str, str]] = []

    def _record(self, level):
        def log(message, *args, **kwargs):
            self.records.append((level, str(message)))

        return log

    def __getattr__(self, name):
        return self._record(name)

    def messages(self, level: str | None = None) -> list[str]:
        return [m for lvl, m in self.records if level is None or lvl == level]


class FakeContainer:
    def __init__(self, name, registry, tracker, wait_result=None, wait_error=None):
        self.name = name
        self.short_id = f"fake{len(registry):04d}"
        self.status = "running"
        self.removed = False
        self._registry = registry
        self._tracker = tracker
        self._wait_result = wait_result if wait_result is not None else {"StatusCode": 0}
        self._wait_error = wait_error

    def wait(self, timeout=None):
        if self._wait_error is not None:
            raise self._wait_error
        if callable(self._wait_result):
            return self._wait_result()
        self.status = "exited"
        return self._wait_result

    def logs(self, stdout=True, stderr=True):
        return b"bot output\n"

    def remove(self, force=False):
        if self.name not in self._registry:
            raise docker.errors.NotFound(f"No such container: {self.name}")
        self._tracker.removed_by_owner.append(self.name)
        del self._registry[self.name]
        self._tracker.live -= 1
        self.removed = True


class VanishingContainer(FakeContainer):
    """Disappeared from the daemon before cleanup ran (reaped elsewhere)."""

    def remove(self, force=False):
        raise docker.errors.NotFound(f"No such container: {self.name}")


class Tracker:
    def __init__(self):
        self.live = 0
        self.max_live = 0
        self.removed_by_owner: list[str] = []


class FakeContainers:
    def __init__(self, tracker, container_factory=None):
        self.registry: dict[str, FakeContainer] = {}
        self.tracker = tracker
        self.container_factory = container_factory or (
            lambda name, registry, tracker: FakeContainer(name, registry, tracker)
        )

    def get(self, name):
        if name not in self.registry:
            raise docker.errors.NotFound(f"No such container: {name}")
        return self.registry[name]

    def run(self, image_tag, **kwargs):
        name = kwargs["name"]
        if name in self.registry:
            raise docker.errors.APIError(
                f'Conflict. The container name "/{name}" is already in use'
            )
        container = self.container_factory(name, self.registry, self.tracker)
        self.registry[name] = container
        self.tracker.live += 1
        self.tracker.max_live = max(self.tracker.max_live, self.tracker.live)
        return container


class FakeDockerClient:
    def __init__(self, tracker, container_factory=None):
        self.containers = FakeContainers(tracker, container_factory)


@pytest.fixture
def tracker():
    return Tracker()


@pytest.fixture(autouse=True)
def recorded_logger(monkeypatch):
    fake = RecordingLogger()
    monkeypatch.setattr(runner, "logger", fake)
    return fake


@pytest.fixture
def client(tracker):
    return FakeDockerClient(tracker)


def run_container(client, container_name="bot_container", score_job_id="job-1"):
    return runner._run_miner_container(
        docker_client=client,
        image_tag="miner:test",
        container_name=container_name,
        labels={"type": "bot-executor-run", "score_job_id": score_job_id},
        environment={"SCORE_JOB_ID": score_job_id},
        network_name="bot-challenge-network",
    )


# ------------------------------ container lifecycle ------------------------ #
def test_successful_run_removes_its_container(client, tracker, recorded_logger):
    logs = run_container(client)

    assert "bot output" in logs
    assert client.containers.registry == {}
    assert tracker.live == 0
    assert "Bot container removed: bot_container" in "\n".join(
        recorded_logger.messages("info")
    )


def test_failed_bot_execution_removes_its_container(tracker):
    def failing(name, registry, tracker_):
        return FakeContainer(name, registry, tracker_, wait_result={"StatusCode": 3})

    client = FakeDockerClient(tracker, container_factory=failing)

    with pytest.raises(ValueError, match="exited with status 3"):
        run_container(client)

    assert client.containers.registry == {}
    assert tracker.live == 0


def test_failed_bot_execution_logs_the_runtime_failure_marker(tracker, recorded_logger):
    def failing(name, registry, tracker_):
        return FakeContainer(name, registry, tracker_, wait_result={"StatusCode": 3})

    client = FakeDockerClient(tracker, container_factory=failing)

    with pytest.raises(ValueError):
        run_container(client)

    # challenges.json greps challenge-api's log for this shape to classify a
    # run as a runtime failure, so the wording is load bearing.
    assert any(
        message.startswith("Bot bot_container failed:")
        for message in recorded_logger.messages("info")
    )


def test_wait_timeout_removes_its_container(tracker):
    def hanging(name, registry, tracker_):
        return FakeContainer(
            name, registry, tracker_, wait_error=requests.exceptions.ReadTimeout("slow")
        )

    client = FakeDockerClient(tracker, container_factory=hanging)

    with pytest.raises(TimeoutError, match="did not exit within"):
        run_container(client)

    assert client.containers.registry == {}
    assert tracker.live == 0


def test_cancellation_removes_its_container(tracker):
    def cancelled(name, registry, tracker_):
        return FakeContainer(name, registry, tracker_, wait_error=KeyboardInterrupt())

    client = FakeDockerClient(tracker, container_factory=cancelled)

    with pytest.raises(KeyboardInterrupt):
        run_container(client)

    assert client.containers.registry == {}


def test_cleanup_is_safe_when_the_container_already_disappeared(
    tracker, recorded_logger
):
    def vanishing(name, registry, tracker_):
        return VanishingContainer(name, registry, tracker_)

    client = FakeDockerClient(tracker, container_factory=vanishing)

    logs = run_container(client)

    assert "bot output" in logs
    assert not any(
        "Could not remove" in message for message in recorded_logger.messages("warning")
    )


def test_a_stale_container_is_reclaimed(client, tracker, recorded_logger):
    # A previous run died before its cleanup could free the shared name.
    stale = FakeContainer("bot_container", client.containers.registry, tracker)
    stale.status = "exited"
    client.containers.registry["bot_container"] = stale
    tracker.live += 1

    logs = run_container(client)

    assert "bot output" in logs
    assert client.containers.registry == {}
    assert any(
        "already allocated" in message
        for message in recorded_logger.messages("warning")
    )


def test_concurrent_runs_are_serialized_and_keep_their_own_containers(tracker):
    def slow(name, registry, tracker_):
        return FakeContainer(
            name,
            registry,
            tracker_,
            wait_result=lambda: (time.sleep(0.2), {"StatusCode": 0})[1],
        )

    client = FakeDockerClient(tracker, container_factory=slow)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker(job_id):
        try:
            results.append(run_container(client, score_job_id=job_id))
        except BaseException as err:  # noqa: BLE001 -- surfaced by the assert below
            errors.append(err)

    threads = [
        threading.Thread(target=worker, args=(f"job-{index}",)) for index in range(2)
    ]
    for thread in threads:
        thread.start()
        time.sleep(0.05)
    for thread in threads:
        thread.join(timeout=10)

    assert errors == []
    assert len(results) == 2
    # One owner at a time, and each run removed exactly its own container.
    assert tracker.max_live == 1
    assert tracker.removed_by_owner == ["bot_container", "bot_container"]
    assert client.containers.registry == {}


# --------------------------------- run_web_bot ----------------------------- #
@pytest.fixture
def captured_run(monkeypatch):
    """Run run_web_bot with the network helpers and the container run stubbed,
    capturing what the bot container would have been given."""
    captured: dict = {}

    def _fake_run(**kwargs):
        captured.update(kwargs)
        return "bot output"

    monkeypatch.setenv("CHALLENGE_HTTPS_PROXY_PORT", "10443")
    monkeypatch.setattr(runner, "get_client", lambda: object())
    monkeypatch.setattr(
        runner, "ensure_phase_network", lambda *a, **k: "bot-challenge-network"
    )
    monkeypatch.setattr(runner, "get_bot_network_gateway", lambda *a, **k: "172.30.0.1")
    monkeypatch.setattr(runner, "_run_miner_container", _fake_run)
    return captured


def test_run_web_bot_points_the_bot_at_the_web_page(captured_run):
    result = runner.run_web_bot(session_count=3, score_job_id="job-x")

    assert result["status"] == "success"
    assert result["sessions_completed"] == 3
    environment = captured_run["environment"]
    assert environment["CHALLENGE_WEB_URL"] == "https://172.30.0.1:10443/_web"
    assert environment["CHALLENGE_BASE_URL"] == "https://172.30.0.1:10443"
    assert environment["BV_SESSION_COUNT"] == 3


def test_run_web_bot_passes_no_simple_bot_environment(captured_run):
    runner.run_web_bot(session_count=1, score_job_id="job-x")

    leftovers = [
        key
        for key in captured_run["environment"]
        if "SIMPLE_BOT" in key or key in {"BOT_FRONTEND_URL", "BOT_BACKEND_URL"}
    ]
    assert leftovers == []


def test_run_web_bot_uses_an_internal_network(monkeypatch, captured_run):
    seen: dict = {}

    def _record_network(_client, network_name, internal):
        seen["network_name"] = network_name
        seen["internal"] = internal
        return network_name

    monkeypatch.setattr(runner, "ensure_phase_network", _record_network)

    runner.run_web_bot(session_count=1)

    # The bot must not be able to reach anything but the challenge proxy.
    assert seen["internal"] is True


# -------------------------------- build_bot_image -------------------------- #
class _ChallengeConfigStub:
    def __init__(self, commit_dir: str) -> None:
        self.commit_dir = commit_dir
        self.miner_image_tag = "miner:test"
        self.challenge_network_name = "bot-challenge-network"
        self.container_run_timeout_sec = 10
        self.container_wait_timeout_sec = 900


class _ConfigStub:
    def __init__(self, commit_dir: str) -> None:
        self.challenge = _ChallengeConfigStub(commit_dir)


class _FakeImages:
    def __init__(self, error=None):
        self.error = error
        self.calls: list[dict] = []

    def build(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return object(), [{"stream": "Step 1/1 : FROM python\n"}]


@pytest.fixture
def build_client(monkeypatch, tmp_path):
    def _install(error=None):
        images = _FakeImages(error)
        client = type("_Client", (), {"images": images})()
        monkeypatch.setattr(runner, "config", _ConfigStub(str(tmp_path)))
        monkeypatch.setattr(runner, "get_client", lambda: client)
        return images

    return _install


def test_build_writes_the_context_and_tags_the_image(build_client, tmp_path):
    images = build_client()

    result = runner.build_bot_image(
        bot_py="print('bot')", dockerfile="FROM python", score_job_id="job-1"
    )

    assert result["status"] == "success"
    assert result["image_tag"] == "miner:test"
    assert (tmp_path / "bot" / "bot.py").read_text() == "print('bot')"
    assert (tmp_path / "bot" / "Dockerfile").read_text() == "FROM python"
    assert images.calls[0]["path"] == str(tmp_path)
    assert images.calls[0]["dockerfile"] == "bot/Dockerfile"
    assert images.calls[0]["tag"] == "miner:test"


def test_build_context_is_reset_between_runs(build_client, tmp_path):
    build_client()
    stale = tmp_path / "bot" / "leftover.py"

    runner.build_bot_image(bot_py="first", dockerfile="FROM python")
    stale.write_text("from a previous submission")
    runner.build_bot_image(bot_py="second", dockerfile="FROM python")

    # A previous miner's files must never survive into the next build.
    assert not stale.exists()
    assert (tmp_path / "bot" / "bot.py").read_text() == "second"


def test_build_failure_surfaces_as_value_error(build_client):
    build_client(error=docker.errors.BuildError("bad dockerfile", build_log=[]))

    with pytest.raises(ValueError, match="Failed to build Docker image"):
        runner.build_bot_image(bot_py="print('bot')", dockerfile="NOT A DOCKERFILE")
