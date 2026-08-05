---
name: bv-score-submission
description: Submit the local Bot Virus Challenge miner submission to a running `/score` endpoint. Use when asked to score, test, or submit `bot.py` and `Dockerfile` from `src/bv_challenge/challenge/commit/bot`, including requests that need the challenge API key.
---

# Submit Bot Virus Score

Run `skills/bv-score-submission/scripts/submit_score.py` from the Bot Virus Challenge repository. It obtains a fresh task, reads `bot.py` and `Dockerfile` from the submission directory, and submits both as the `miner_output` payload.

## Workflow

1. Confirm the challenge API is running and obtain an authorized key without printing it.
2. Pass the key through `BV_CHALLENGE_API_KEY` or `--api-key`; prefer the environment variable.
3. Run the script. It requests `GET /task`, then sends `POST /score` with `X-API-Key`.
4. Report the response body and status. On failure, do not reveal the key; use `--verbose` for safe HTTP diagnostics.

```sh
export BV_CHALLENGE_API_KEY='replace-with-authorized-key'
python3 skills/bv-score-submission/scripts/submit_score.py \
  --base-url http://localhost:10001
```

Use `--submission-dir` only when the submission is not at `src/bv_challenge/challenge/commit/bot`. The directory must contain the required `bot.py` and `Dockerfile` inputs. Never modify either file merely to submit it.

## Failure handling

- `401`: key missing, invalid, or not authorized; verify the deployed `BV_CHALLENGE_API_KEY` privately.
- `422`: inspect the server response; ensure both submission files exist and are non-empty.
- connection failure: start the API or pass the correct `--base-url`.
- non-2xx scoring error: preserve the response for diagnosis; do not retry automatically because scoring builds/runs containers.
