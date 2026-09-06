import pytest
from docker.errors import DockerException

from src.bv_challenge.challenge.api.endpoints.challenge import docker_client


class _FakeDaemon:
    """Refuses the first `failures` pings, then answers. Doubles as the
    from_env factory so the test can count reconnects."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.ping_count = 0
        self.from_env_count = 0

    def from_env(self) -> "_FakeDaemon":
        self.from_env_count += 1
        return self

    def ping(self) -> bool:
        self.ping_count += 1
        if self.ping_count <= self.failures:
            raise DockerException("daemon is not up yet")
        return True


@pytest.fixture(autouse=True)
def _clear_cached_client():
    docker_client.reset_client()
    yield
    docker_client.reset_client()


@pytest.fixture
def install_daemon(monkeypatch):
    def _install(failures: int) -> _FakeDaemon:
        daemon = _FakeDaemon(failures)
        monkeypatch.setattr(docker_client.docker, "from_env", daemon.from_env)
        return daemon

    return _install


def test_returns_as_soon_as_the_daemon_answers(install_daemon) -> None:
    daemon = install_daemon(failures=0)

    docker_client.wait_for_daemon(timeout_sec=5, interval_sec=0)

    assert daemon.ping_count == 1


def test_retries_until_the_daemon_answers(install_daemon) -> None:
    daemon = install_daemon(failures=3)

    docker_client.wait_for_daemon(timeout_sec=5, interval_sec=0)

    assert daemon.ping_count == 4


def test_raises_when_the_daemon_never_answers(install_daemon) -> None:
    daemon = install_daemon(failures=10_000)

    with pytest.raises(TimeoutError):
        docker_client.wait_for_daemon(timeout_sec=0, interval_sec=0)

    # A zero timeout still buys one real attempt, never zero.
    assert daemon.ping_count == 1


def test_a_refused_connection_is_not_cached(install_daemon) -> None:
    daemon = install_daemon(failures=2)

    docker_client.wait_for_daemon(timeout_sec=5, interval_sec=0)

    # Each failed ping drops the client, so the next attempt reconnects rather
    # than reusing a dead one.
    assert daemon.from_env_count == 3


def test_error_names_the_socket_it_tried(install_daemon, monkeypatch) -> None:
    install_daemon(failures=10_000)
    monkeypatch.setenv("DOCKER_HOST", "unix:///docker-socket/docker.sock")

    with pytest.raises(TimeoutError, match="/docker-socket/docker.sock"):
        docker_client.wait_for_daemon(timeout_sec=0, interval_sec=0)


def test_timeout_falls_back_to_the_default_when_env_is_unset(monkeypatch) -> None:
    monkeypatch.delenv("BV_CHALLENGE_DOCKER_READY_TIMEOUT_SEC", raising=False)

    assert docker_client._timeout_from_env() == 120.0


def test_timeout_reads_the_env_override(monkeypatch) -> None:
    monkeypatch.setenv("BV_CHALLENGE_DOCKER_READY_TIMEOUT_SEC", "7.5")

    assert docker_client._timeout_from_env() == 7.5
