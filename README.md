# Bot Virus Challenge

Bot Virus Challenge is a RedTeam Subnet evaluation service for miner-supplied bot submissions. It builds a submitted miner image, runs it through the configured bot checks, and returns scoring feedback.

Canonical product docs: <https://docs.theredteam.io/latest/challenges/bot-virus-challenge>.

## Components

The default Compose stack starts three services:

| Component | Role |
| --- | --- |
| `challenge-api` | Public challenge API: supplies tasks, accepts miner outputs, builds and runs the miner container, and returns results. |
| `bot-runner-dind` | Docker-in-Docker daemon that hosts miner images and bot containers, plus the HTTPS proxy the bot uses to reach the challenge page. |

`challenge-api` drives the bot lifecycle itself over the DinD socket at `DOCKER_HOST` (default: `unix:///docker-socket/docker.sock`). The build context is a volume shared with the daemon, mounted at the same path on both sides because the daemon, not the API, resolves the bot's bind mounts.

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
| `DOCKER_HOST` | `unix:///docker-socket/docker.sock` | DinD socket `challenge-api` builds and runs bot containers on. |
| `CHALLENGE_HTTPS_PROXY_PORT` | `10443` | Port of the DinD HTTPS proxy the bot uses to reach `/_web`. |
| `BV_CHALLENGE_COMMIT_DIR` | `/commit` | Bot build context, shared with the DinD daemon at the same path. |
| `BV_CHALLENGE_CONTAINER_WAIT_TIMEOUT_SEC` | `900` | How long to wait for a bot container to exit before failing the run. |
| `BV_CHALLENGE_DOCKER_READY_TIMEOUT_SEC` | `120` | How long the entrypoint waits for the DinD daemon at startup. |

## Miner evaluation workflow

1. A miner retrieves the task from `GET /task`.
2. The miner produces the required output and sends it to `POST /score` together with the corresponding task input and an API key.
3. The challenge API builds the supplied bot/Dockerfile and runs the configured number of challenge-web sessions against its own `/_web` page.
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

- A failed build or web session is reflected in the score according to the challenge configuration. `challenge-api` logs the whole lifecycle, so diagnosing it no longer means correlating two services.
- `challenge-api` needs the DinD socket, the shared commit volume, and the configured Docker networks. A missing mount or network commonly appears as a build or run failure. The entrypoint fails fast if the daemon does not answer.
- `challenge-api` builds and runs untrusted containers, on the nested daemon rather than the host one. Keep the stack on a controlled network, and place it behind network controls and an authentication gateway if exposed.
- Validate configuration before rollout with `./compose.sh validate`; check service readiness with `/health` and inspect logs via `./compose.sh logs -f`.

## Development and references

```sh
pip install -e .[dev]
pre-commit install
```

- [Miner commit example](examples/miner_commit/README.md)
- [Score submission skill](skills/bv-score-submission/SKILL.md)
- [Release notes](docs/release-notes.md)
