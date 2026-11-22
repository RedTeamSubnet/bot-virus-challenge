# Bot Virus Challenge

Bot Virus Challenge is a RedTeam Subnet evaluation service for miner-supplied bot submissions. It builds a submitted miner image, runs it through the configured bot checks, and returns scoring feedback.

Canonical product docs: <https://docs.theredteam.io/latest/challenges/bot-virus-challenge>.

## Components

The default Compose stack starts three services:

| Component | Role |
| --- | --- |
| `challenge-api` | Public challenge API: supplies tasks, accepts miner outputs, coordinates evaluation, and returns results. |
| `bot-runner` | Bot Runner: builds and executes submitted miner containers for the challenge API. |
| `bot-runner-dind` | Docker-in-Docker daemon used by the Bot Runner. |

The Bot Runner is an internal service. Its implementation is not part of this public repository; miners only need the submission contract described below and in the [miner commit example](examples/miner_commit/README.md).

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

## Miner evaluation workflow

1. A miner retrieves the task from `GET /task`.
2. The miner produces the required output and sends it to `POST /score` together with the corresponding task input and an API key.
3. The evaluation service builds the submitted bot image, runs the simple-bot gate when enabled, then runs the challenge web sessions when enabled.
4. The miner or operator reads the latest feedback from `GET /result`.

Submission requirements (file layout, Ruff compliance, bot page actions, and allowed Dockerfile sources) are listed in the [miner commit example](examples/miner_commit/README.md#-submission-requirements).

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

- A failed build, simple-bot gate, or web session is reflected in the score according to the challenge configuration. Check the service logs (`./compose.sh logs -f`) when diagnosing it.
- Validate configuration before rollout with `./compose.sh validate`, and check service readiness with `/health`.
- Miner submissions are untrusted code. Run the stack on a controlled host and keep the score API key out of version control.

## Development and references

```sh
pip install -e .[dev]
pre-commit install
```

- [Miner commit example and submission requirements](examples/miner_commit/README.md)
- [Score submission skill](skills/bv-score-submission/SKILL.md)
- [Release notes](docs/release-notes.md)
