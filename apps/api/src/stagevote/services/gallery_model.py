"""The vision model, on this machine (GALLERY_MODEL_URL; empty turns it off). Nothing goes elsewhere.

It answers two things. Do two small aligned face crops show the same person? Every failure answers
None, which the matching rules treat as "not the same person", so a busy model can never stop the
queue. And what does a file show? That is asked only when an organiser opens a file's analysis, with
small copies of the file, so it adds no load until someone looks.

From testing on real event photos: with a free-form answer the model often writes an essay instead
of a verdict, so the JSON is forced by a schema, and its low-confidence yeses are often wrong, so a
yes counts only from MIN_CONFIDENCE.
"""

from __future__ import annotations

import base64
import json
from typing import Any

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


DESCRIBE_TIMEOUT_SECONDS = 90.0
DESCRIBE_PROMPT = (
    "These pictures are from one file in an event's photo album. In one or two plain sentences, say what "
    "is happening and where. Count the people you can see. Do not name or identify anyone."
)
DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, "people": {"type": "integer", "minimum": 0}},
    "required": ["summary", "people"],
    "additionalProperties": False,
}
SUMMARY_MAX = 800


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


async def _ask(content: list[Any], schema: dict[str, Any], max_tokens: int, seconds: float) -> str | None:
    """The model's text answer in the given JSON shape, or None if it did not answer."""
    settings = get_settings()
    if not settings.gallery_model_url:
        return None
    body = {
        "model": settings.gallery_model_name,
        "max_tokens": max_tokens,
        "temperature": 0,
        "chat_template_kwargs": {"enable_thinking": False},
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": True},
        },
        "messages": [{"role": "user", "content": content}],
    }
    url = settings.gallery_model_url.rstrip("/") + "/chat/completions"
    try:
        async with httpx.AsyncClient(timeout=seconds) as client:
            response = await client.post(url, json=body)
            response.raise_for_status()
            return str(response.json()["choices"][0]["message"]["content"] or "")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        log.warning("vision model did not answer", extra={"error": type(exc).__name__})
        return None


async def same_person(first: bytes, second: bytes) -> bool | None:
    content = [{"type": "text", "text": PROMPT}, _image(first), _image(second)]
    text = await _ask(content, SCHEMA, 40, TIMEOUT_SECONDS)
    if text is None:
        return None
    answer = read_answer(text)
    if answer is None:
        log.warning("vision model answer was not readable")
    return answer


def read_description(content: str) -> dict[str, object] | None:
    try:
        answer = json.loads(content)
        summary = " ".join(str(answer["summary"]).split())[:SUMMARY_MAX]
        people = int(answer["people"])
    except (ValueError, TypeError, KeyError):
        return None
    return {"summary": summary, "people": max(0, people)} if summary else None


async def describe(pictures: list[bytes]) -> dict[str, object] | None:
    """What a photo or a few frames of a video show, or None if the model did not answer."""
    content = [{"type": "text", "text": DESCRIBE_PROMPT}, *[_image(picture) for picture in pictures]]
    text = await _ask(content, DESCRIBE_SCHEMA, 220, DESCRIBE_TIMEOUT_SECONDS)
    return read_description(text) if text is not None else None
