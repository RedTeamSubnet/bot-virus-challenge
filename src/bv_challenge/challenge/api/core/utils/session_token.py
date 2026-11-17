# -*- coding: utf-8 -*-
"""Session tokens issued to the challenge page.

Proof that the page was served by this challenge for a given session. The
scorer (module-rt-score ``scoring.session_token``) verifies it with the SAME
secret, so this must stay byte-identical:

    token = base64url(HMAC-SHA256(secret, f"{session_id}.{issued_at}"))

``issued_at`` is whole Unix seconds. The secret is shared with the scorer only
and is never sent to the browser; only the derived token is.
"""

import base64
import hashlib
import hmac
import time


def issue_session_token(session_id: str, secret: str, issued_at: int) -> str:
    _mac = hmac.new(
        secret.encode(), f"{session_id}.{int(issued_at)}".encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(_mac).rstrip(b"=").decode()


def issue_now(session_id: str, secret: str) -> tuple[str, int]:
    """(token, issued_at) for ``session_id`` at the current whole second."""
    _issued_at = int(time.time())
    return issue_session_token(session_id, secret, _issued_at), _issued_at


__all__ = ["issue_now", "issue_session_token"]
