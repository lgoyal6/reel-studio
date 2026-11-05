"""Access gate for an invited pilot, with signed browser-owned workspaces."""
from collections import defaultdict, deque
import hmac
import os
from pathlib import Path
import secrets
import time

from pydantic import BaseModel, Field


class SessionRequest(BaseModel):
    access_code: str = Field(min_length=1, max_length=256)


def signing_secret(root: Path) -> str:
    configured = os.environ.get("REEL_SESSION_SECRET")
    if configured:
        if len(configured) < 32:
            raise ValueError("REEL_SESSION_SECRET needs at least 32 characters")
        return configured
    path = root / ".session-secret"
    if not path.exists():
        with path.open("x") as file:
            os.chmod(path, 0o600)
            file.write(secrets.token_urlsafe(48))
    return path.read_text().strip()


class AccessGate:
    def __init__(self, origin: str | None, code: str | None):
        self.origin = origin.rstrip("/") if origin else None
        self.code = code
        if self.origin and (not code or len(code) < 12):
            raise ValueError("Hosted mode requires a REEL_DEMO_ACCESS_CODE with at least 12 characters")
        self.attempts: dict[str, deque] = defaultdict(deque)

    @property
    def enabled(self):
        return bool(self.origin)

    def check(self, address: str, supplied: str) -> bool:
        attempts = self.attempts[address]
        cutoff = time.monotonic() - 60
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        if len(attempts) >= 8:
            raise ValueError("Too many attempts. Wait one minute and try again.")
        attempts.append(time.monotonic())
        valid = hmac.compare_digest(supplied.encode(), (self.code or "").encode())
        if valid:
            attempts.clear()
        return valid
