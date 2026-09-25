"""Token authentication that works for scripts and for a browser.

Scripts send ``Authorization: Bearer <token>``. Browsers get HTTP Basic: the
401 carries ``WWW-Authenticate: Basic``, so the browser shows its own login
prompt (any username, the token as password) and resends it on later
requests, including the page's own ``fetch`` calls. No cookies, sessions or
login page are needed.
"""

import base64
import binascii
import secrets
from typing import Final

from fastapi import HTTPException, Request, status

REALM: Final = 'Basic realm="apply-agent", charset="UTF-8"'


def _presented_token(header: str) -> str | None:
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer":
        return value.strip() or None
    if scheme.lower() == "basic":
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return decoded.partition(":")[2] or None  # username is ignored
    return None


class TokenAuth:
    def __init__(self, token: str) -> None:
        if not token:
            raise ValueError("the API token must not be empty")
        self._token = token.encode()

    def __call__(self, request: Request) -> None:
        presented = _presented_token(request.headers.get("authorization", ""))
        # Constant-time comparison: response timing must not leak the token.
        if presented is None or not secrets.compare_digest(presented.encode(), self._token):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="missing or invalid API token",
                headers={"WWW-Authenticate": REALM},
            )
