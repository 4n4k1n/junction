"""One Claude call that must answer with JSON matching a schema."""
from __future__ import annotations

import json
import os
from typing import List

MODEL = os.getenv("LEARNLENS_MODEL", "claude-opus-5-5")
# Short calls (reading a request, looking at a photo). With one retry this stays under the app's
# request timeout, so a slow AI service ends in the keyword fallback, not in a failed request.
TIMEOUT_S = float(os.getenv("LEARNLENS_LLM_TIMEOUT", "20"))


def make_client(timeout: float = TIMEOUT_S):
    import anthropic  # imported lazily so tests and the mocks run without the SDK configured

    return anthropic.Anthropic(timeout=timeout, max_retries=1)


last_error = ""  # why the most recent call to the AI service failed; empty after a call that worked


def ask_json(client, system: List[dict], content: List[dict], schema: dict, effort: str,
             max_tokens: int = 8000) -> dict:
    global last_error
    try:
        response = _create(client, system, content, schema, effort, max_tokens)
    except Exception as e:
        last_error = f"{type(e).__name__}: {str(e)[:300]}"  # shown by GET /health (e.g. a used-up budget)
        raise
    last_error = ""
    if response.stop_reason == "refusal":
        raise RuntimeError(f"model refused: {getattr(response.stop_details, 'category', None)}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("model hit max_tokens")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def _create(client, system: List[dict], content: List[dict], schema: dict, effort: str, max_tokens: int):
    return client.beta.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",  # on a safety refusal the API retries on a fallback model
    )
