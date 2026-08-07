# Container Runner

Container Runner is the privileged execution service used by Bot Virus Challenge. It receives a miner submission from the challenge controller, builds its container image, then runs that image against the configured simple-bot and challenge-web targets.

It is packaged under `rest.mdm-sn-container-runner`. Some existing config names, log directories, and service labels retain the historical `vm-runner` name; they refer to this same service.

## Architecture

```text
Miner submission
      |
      v
Challenge API  -- internal HTTP -->  Container Runner  -- Docker socket --> Docker-in-Docker
      |                                      |
      +---------- challenge result ----------+--> simple-bot / challenge-web targets
```

The runner relies on:

- a shared commit workspace (`MDM_CHALLENGE_COMMIT_DIR`, `/commit` in the root Compose stack);
- a Docker daemon via `DOCKER_HOST` (the root stack uses `unix:///docker-socket/docker.sock`);
- the simple-bot and challenge Docker networks; and
- access to the challenge API via `MDM_CHALLENGE_BASE_URL`.

For an integrated deployment, use the root repository’s [Compose stack](../../../README.md). It starts `challenge-api`, `bot-runner`, and `bot-runner-dind` with the required volumes and networks.

## Run locally

The root Compose stack is the supported operational path:

```sh
cd ../../..
cp .env.example .env
./compose.sh validate
./compose.sh start -l
```

The runner is published on `VM_RUNNER_API_PORT` (default `8000`) in that stack:

```sh
curl -s http://localhost:8000/health | jq
```

Standalone development additionally requires a reachable Docker daemon, the commit workspace, both required networks, and a reachable challenge API. Starting the Python process alone does not supply those dependencies.

## Configuration

Set deployment values through the root `.env` file or service environment. Never commit real credentials or production-only URLs.

| Variable | Default | Purpose |
| --- | --- | --- |
| `VM_RUNNER_API_PORT` | `8000` | HTTP port for the runner. |
| `MDM_CHALLENGE_BASE_URL` | required | Challenge API URL the runner calls while executing checks. |
| `MDM_CHALLENGE_COMMIT_DIR` | `/commit` | Shared miner-commit workspace. |
| `MDM_CHALLENGE_SIMPLE_BOT_NETWORK_NAME` | `bot-simple-network` | Docker network for the simple-bot phase. |
| `MDM_CHALLENGE_CHALLENGE_NETWORK_NAME` | `bot-challenge-network` | Docker network for challenge-web sessions. |
| `MDM_CHALLENGE_MINER_IMAGE_TAG` | `redteamsubnet/bv-miner:latest` | Image tag used for the submitted miner. |
| `MDM_CHALLENGE_SIMPLE_BOT_URL` | `https://simplebot.theredteam.io` | Simple-bot target URL. |
| `MDM_CHALLENGE_SIMPLE_BOT_POLL_MAX_ATTEMPTS` | `5` | Maximum simple-bot result polls. |
| `MDM_CHALLENGE_SIMPLE_BOT_POLL_INTERVAL_SEC` | `2` | Seconds between simple-bot polls. |
| `MDM_CHALLENGE_CONTAINER_RUN_TIMEOUT_SEC` | `10` | Per-container run timeout. |
| `DOCKER_HOST` | daemon default | Docker socket/daemon used to build and run images. |

Use Compose service names inside Compose networks (`http://challenge-api:10001`) and `localhost` only for host-local processes.

## API workflow

The API has no prefix in the current configuration. Full request/response schemas are served by the running instance:

- Swagger UI: `http://<runner-host>:8000/docs`
- ReDoc: `http://<runner-host>:8000/redoc`
- OpenAPI JSON: `http://<runner-host>:8000/openapi.json`

Call the endpoints in order:

1. `GET /health` — confirms runner readiness.
2. `POST /build` — accepts `bot_py`, `dockerfile`, and optional `score_job_id`; builds the miner image.
3. `POST /run-simple-bot` — runs the already-built image against the simple-bot target. `timeout_sec` and `score_job_id` are optional controls.
4. `POST /run-web` — runs the already-built image against the challenge webpage. `session_count` and `score_job_id` are optional controls.

Example health check:

```sh
curl -s http://localhost:8000/health | jq
```

Use the running OpenAPI document for build/run payloads rather than copying submission content into shell history. The challenge controller is the normal API client; direct calls are primarily useful for controlled diagnostics.

## Security and operations

Container Runner builds and executes untrusted miner containers and requires Docker-daemon access. Treat it as a sensitive execution boundary.

- Prefer an internal-only runner reachable from `challenge-api` over a dedicated network.
- If external exposure is required, put the service behind restrictive network controls and an authentication gateway; do not publish it directly to the Internet.
- Run the Docker-in-Docker daemon only where privileged-container access is acceptable, and protect the shared commit volume.
- Keep `bot-runner` and `bot-runner-dind` logs when investigating failed builds/runs; pair them with `challenge-api` logs using the request/job ID.
- Before deployment, run `./compose.sh validate` from the repository root and verify both `/health` endpoints after startup.

## Testing

See [docs/TESTING.md](docs/TESTING.md) for local test setup and commands.
