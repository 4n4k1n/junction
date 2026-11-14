"""Verifies the deployment approval token minted by the backend (backend/app/build/approval.py).

A token is base64url(JSON {build_id, sha256, exp, nonce}) + "." + hex HMAC-SHA256. It is valid
for one build, one exact sketch, a few minutes, and one upload.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Set

KEY_FILE = Path(os.getenv("LEARNLENS_APPROVAL_KEY_FILE", Path.home() / ".learnlens" / "approval.key"))


class ApprovalError(Exception):
    pass


def load_key() -> bytes:
    env = os.getenv("LEARNLENS_APPROVAL_SECRET")
    if env:
        return env.encode()
    if not KEY_FILE.exists():
        raise ApprovalError(f"no approval key: set LEARNLENS_APPROVAL_SECRET or start the backend once ({KEY_FILE})")
    return KEY_FILE.read_text().strip().encode()


def verify(token: str, build_id: str, sketch_sha256: str, used_nonces: Set[str]) -> dict:
    """Returns the payload, or raises ApprovalError. Marks the token as used."""
    if not token:
        raise ApprovalError("approval token missing: the user has not approved this sketch")
    body, _, signature = token.partition(".")
    expected = hmac.new(load_key(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ApprovalError("approval token signature is not valid")
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode()))
        exp, nonce = int(payload["exp"]), str(payload["nonce"])
    except (ValueError, KeyError, TypeError):
        raise ApprovalError("approval token is malformed")
    if payload.get("build_id") != build_id:
        raise ApprovalError("approval token is for a different build")
    if payload.get("sha256") != sketch_sha256:
        raise ApprovalError("approval token is for a different sketch than the one compiled")
    if time.time() > exp:
        raise ApprovalError("approval token has expired: approve again in the app")
    if nonce in used_nonces:
        raise ApprovalError("approval token was already used: approve again in the app")
    used_nonces.add(nonce)
    return payload
