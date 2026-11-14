"""One Claude call that must answer with JSON matching a schema."""
from __future__ import annotations

import json
import os
from typing import List

MODEL = os.getenv("LEARNLENS_MODEL", "claude-opus-5-5")
TIMEOUT_S = float(os.getenv("LEARNLENS_LLM_TIMEOUT", "30"))


def make_client(timeout: float = TIMEOUT_S):
    import anthropic  # imported lazily so tests and the mocks run without the SDK configured

    return anthropic.Anthropic(timeout=timeout, max_retries=1)


def ask_json(client, system: List[dict], content: List[dict], schema: dict, effort: str,
             max_tokens: int = 8000) -> dict:
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # on a safety refusal the API retries on a fallback model
    )
    if response.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {getattr(response.stop_details, 'category', None)}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("model hit max_tokens")
    return json.loads(next(b.text for b in response.content if b.type == "text"))
