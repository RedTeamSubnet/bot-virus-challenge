# Bot Virus Challenge

Bot Virus Challenge is a RedTeam Subnet evaluation service for miner-supplied bot submissions. It builds a submitted miner image, runs it through the configured bot checks, and returns scoring feedback.

Canonical product docs: <https://docs.theredteam.io/latest/challenges/bot-virus-challenge>.

## Components

The default Compose stack starts three services:

| Component | Role |
| --- | --- |
| `challenge-api` | Public challenge API: supplies tasks, accepts miner outputs, coordinates evaluation, and returns results. |
| `bot-runner` | Container Runner: builds and executes submitted miner containers for the challenge API. |
| `bot-runner-dind` | Docker-in-Docker daemon used by the Container Runner. |

The challenge API calls the Container Runner internally at `BV_CHALLENGE_API_BOT_RUNNER_URL` (default: `http://bot-runner:8000`). See the [Container Runner README](src/modules/bv-bot-runner/README.md) for its operational contract.

## Quick start

Prerequisites: Docker Engine and Docker Compose. Python 3.10+ is needed only for local development.

```sh
git clone https://github.com/RedTeamSubnet/bot-virus-challenge.git
cd bot-virus-challenge
cp .env.example .env
./compose.sh validate
./compose.sh start -l
```

Equivalent Compose commands:

```sh
docker compose config
docker compose up -d --remove-orphans --force-recreate
docker compose logs -f -n 100
```

The default public API port is `10001`.

```sh
curl -s http://localhost:10001/health | jq
curl -s http://localhost:10001/openapi.json | jq
```

- Swagger UI: <http://localhost:10001/docs>
- ReDoc: <http://localhost:10001/redoc>
- OpenAPI JSON: <http://localhost:10001/openapi.json>

Stop the stack when finished:

```sh
./compose.sh stop
# or: docker compose down --remove-orphans
```

## Configuration

Copy `.env.example` before changing values. Keep secrets and deployment-specific values outside version control.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BV_CHALLENGE_API_PORT` | `10001` | Public challenge API port. |
| `BV_CHALLENGE_API_KEY` | required | API key required by `POST /score`; use 9–128 alphanumeric or hyphen characters. |
| `BV_CHALLENGE_API_BOT_RUNNER_URL` | `http://bot-runner:8000` | Container Runner URL as seen by `challenge-api`. |
| `BV_CHALLENGE_API_BOT_RUNNER_SESSION_COUNT` | `2` | Number of web-session checks requested from the runner. |
| `BV_CHALLENGE_API_BOT_RUNNER_REQUEST_TIMEOUT_SEC` | `900` | Controller-to-runner request timeout. |
| `MDM_CHALLENGE_BASE_URL` | `http://challenge-api:10001` | Challenge API base URL as seen by the runner. |
| `VM_RUNNER_API_PORT` | `8000` | Container Runner port. |

For host-local development, use `http://localhost:10001` where a runner process cannot resolve the Compose service name. For the full Compose stack, retain the service-name defaults.

## Miner evaluation workflow

1. A miner retrieves the task from `GET /task`.
2. The miner produces the required output and sends it to `POST /score` together with the corresponding task input and an API key.
3. The challenge API asks the Container Runner to build the supplied bot/Dockerfile, run the simple-bot gate when enabled, then run configured challenge-web sessions when enabled.
4. The miner or operator reads the latest feedback from `GET /result`.

Use Swagger for exact request and response schemas; they are versioned with the running API. Basic discovery calls:

```sh
curl -s http://localhost:10001/task | jq
curl -s http://localhost:10001/result | jq
```

`POST /score` requires the `miner_input` and `miner_output` models returned/defined by the API. Do not hand-copy stale payload shapes: use `/docs` or `/openapi.json` from the deployed version.

### Score API authentication

`POST /score` requires `X-API-Key`; `GET /task`, `GET /result`, health, and documentation endpoints remain unauthenticated. Set a strong value in `BV_CHALLENGE_API_KEY` before starting the service and distribute it only to authorized score clients.

```sh
curl -sS -X POST http://localhost:10001/score \
  -H "X-API-Key: $BV_CHALLENGE_API_KEY" \
  -H 'Content-Type: application/json' \
  --data @score-payload.json | jq
```

The API returns `401` for a missing or invalid key. Keys must be 9–128 characters and contain only letters, digits, and hyphens. Keep the key in an environment variable or secret manager; never put it in a committed payload, URL, or shell history.

## Operations and troubleshooting

- A failed build, simple-bot gate, web session, or runner request is reflected in the score according to the challenge configuration. Inspect `challenge-api` and `bot-runner` logs together when diagnosing it.
- The runner needs access to the shared commit directory, its Docker socket, and the configured Docker networks. A missing mount/network commonly appears as a build or run failure.
- The runner API can build and run untrusted containers. Keep it on a controlled network when possible. If exposed outside that network, place it behind appropriate network controls and an authentication gateway.
- Validate configuration before rollout with `./compose.sh validate`; check service readiness with `/health` and inspect logs via `./compose.sh logs -f`.

## Development and references

```sh
pip install -e .[dev]
pre-commit install
```

- [Container Runner operations](src/modules/bv-bot-runner/README.md)
- [Container Runner tests](src/modules/bv-bot-runner/docs/TESTING.md)
- [Miner commit example](examples/miner_commit/README.md)
- [Score submission skill](skills/bv-score-submission/SKILL.md)
- [Release notes](docs/release-notes.md)
