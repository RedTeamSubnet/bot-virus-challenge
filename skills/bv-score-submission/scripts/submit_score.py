#!/usr/bin/env python3
"""Submit a local Bot Virus Challenge miner submission to /score."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SUBMISSION_DIR = REPOSITORY_ROOT / "src/bv_challenge/challenge/commit/bot"


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: object | None = None,
) -> tuple[int, object]:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers=headers or {},
        method="POST" if data else "GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except HTTPError as error:
        raw = error.read().decode("utf-8", errors="replace")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, {"detail": raw}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:10001", help="Challenge API base URL.")
    parser.add_argument(
        "--api-key",
        default=os.environ.get("BV_CHALLENGE_API_KEY"),
        help="API key; defaults to BV_CHALLENGE_API_KEY.",
    )
    parser.add_argument(
        "--submission-dir",
        type=Path,
        default=DEFAULT_SUBMISSION_DIR,
        help="Directory containing bot.py and Dockerfile.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print request URLs and HTTP statuses; never prints the API key.",
    )
    return parser.parse_args()


def read_submission(path: Path) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    for name in ("bot.py", "Dockerfile"):
        target = path / name
        if not target.is_file():
            raise ValueError(f"Missing required submission file: {target}")
        content = target.read_text(encoding="utf-8")
        if not content.strip():
            raise ValueError(f"Submission file is empty: {target}")
        files.append({"file_name": name, "content": content})
    return files


def main() -> int:
    args = parse_args()
    if not args.api_key:
        print("BV_CHALLENGE_API_KEY or --api-key is required.", file=sys.stderr)
        return 2
    base_url = args.base_url.rstrip("/")
    try:
        commit_files = read_submission(args.submission_dir)
        if args.verbose:
            print(f"GET {base_url}/task", file=sys.stderr)
        task_status, miner_input = request_json(f"{base_url}/task")
        if task_status != 200 or not isinstance(miner_input, dict):
            print(
                json.dumps({"status": task_status, "response": miner_input}, indent=2),
                file=sys.stderr,
            )
            return 1
        payload = {
            "miner_input": miner_input,
            "miner_output": {"commit_files": commit_files},
        }
        if args.verbose:
            print(f"POST {base_url}/score", file=sys.stderr)
        score_status, score_response = request_json(
            f"{base_url}/score",
            headers={"Content-Type": "application/json", "X-API-Key": args.api_key},
            body=payload,
        )
    except (OSError, URLError, ValueError) as error:
        print(f"Submission failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(score_response, indent=2))
    return 0 if 200 <= score_status < 300 else 1


if __name__ == "__main__":
    raise SystemExit(main())
