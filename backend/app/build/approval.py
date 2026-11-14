"""Deployment approval tokens.

When the user approves a sketch, the backend signs a short-lived token that names the build and
the sketch's sha256. The Mac deploy agent refuses to upload without a valid one, so neither a
generated sketch nor a compromised caller of the agent's local API can trigger a flash.

Format: base64url(JSON payload) + "." + hex HMAC-SHA256. agent/lldeploy/approval.py verifies it
(kept separate because the agent uses the standard library only).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path

TTL_S = int(os.getenv("LEARNLENS_APPROVAL_TTL", "300"))
KEY_FILE = Path(os.getenv("LEARNLENS_APPROVAL_KEY_FILE", Path.home() / ".learnlens" / "approval.key"))


def load_key() -> bytes:
    """LEARNLENS_APPROVAL_SECRET, else a key file shared with the agent on the same Mac (created 0600)."""
    env = os.getenv("LEARNLENS_APPROVAL_SECRET")
    if env:
        return env.encode()
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    return KEY_FILE.read_text().strip().encode()


def mint(build_id: str, sketch_sha256: str, ttl_s: int = TTL_S) -> str:
    payload = {"build_id": build_id, "sha256": sketch_sha256, "exp": int(time.time()) + ttl_s,
               "nonce": secrets.token_hex(8)}
    body = base64.urlsafe_b64encode(json.dumps(payload, sort_keys=True).encode()).decode()
    return body + "." + hmac.new(load_key(), body.encode(), hashlib.sha256).hexdigest()
