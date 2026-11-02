"""The vision model's opinion on one close call: do these two face crops show the same person?

Only the two small aligned crops are sent, and only to the configured model on this machine
(GALLERY_MODEL_URL); leaving that empty turns the question off. Every failure answers None, which
the matching rules treat as "not the same person", so a busy model can never stop the queue.

From testing on real event photos: with a free-form answer the model often writes an essay instead
of a verdict, so the JSON is forced by a schema, and its low-confidence yeses are often wrong, so a
yes counts only from MIN_CONFIDENCE.
"""

from __future__ import annotations

import base64
import json

import httpx

from ..config import get_settings
from ..logging import get_logger

log = get_logger(__name__)

MIN_CONFIDENCE = 0.9
TIMEOUT_SECONDS = 30.0
PROMPT = (
    "Two aligned face crops from an event photo album. Lighting, size and sharpness can differ. "
    "Compare facial structure only: eye shape and spacing, nose, jaw, face shape, facial hair pattern. "
    "Most pairs shown are different people who look alike, so answer same_person true only when the "
    "structure clearly matches."
)
SCHEMA = {
    "type": "object",
    "properties": {
        "same_person": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["same_person", "confidence"],
    "additionalProperties": False,
}


def _image(jpeg: bytes) -> dict[str, object]:
    url = "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()
    return {"type": "image_url", "image_url": {"url": url}}


def read_answer(content: str) -> bool | None:
    """The model's text as a decision, or None when it is not the JSON that was asked for."""
    try:
        answer = json.loads(content)
        same = answer["same_person"]
        confidence = float(answer["confidence"])
    except (ValueError, TypeError, KeyError):
        return None
    if not isinstance(same, bool):
        return None
    return same and confidence >= MIN_CONFIDENCE


async def same_person(first: bytes, second: bytes) -> bool | None:
    settings = get_settings()
    if not settings.gallery_model_url:
        return None
    body = {
        "model": settings.gallery_model_name,
        "max_tokens": 40,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "face_match", "schema": SCHEMA, "strict": True},
        },
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": PROMPT}, _image(first), _image(second)]}
        ],
    }
    url = settings.gallery_model_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("vision model did not answer", extra={"error": type(exc).__name__})
        return None
    answer = read_answer(content or "")
    if answer is None:
        log.warning("vision model answer was not readable")
    return answer
