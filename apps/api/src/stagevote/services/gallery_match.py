"""Who a face belongs to: the rules, on plain numbers, with no database or model behind them.

Two faces are compared by the cosine of their SFace descriptions (1 is identical). From the
OpenCV docs, 0.363 separates the same person from different people on clean portraits. Event
photos are harder, so a score settles it only when it is clear: from MATCH_AT up it is the same
person, below ASK_AT it is not, and the band between goes to the vision model. An unanswered
question counts as "not the same person": a photo missing from someone's page is a far smaller
mistake than a photo of someone else on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Literal

import numpy as np

EMBEDDING_DTYPE = np.float32
MATCH_AT = 0.50
ASK_AT = 0.36
# A second selfie this close is the same person on another phone: the phone joins their page.
SAME_SELFIE_AT = 0.60
# A person is compared through their selfie and up to this many of their clearest faces.
REFERENCES_PER_PERSON = 12
# How many people a borderline face may be put to the model about.
MAX_CANDIDATES = 2

Verdict = Literal["match", "ask", "no"]


@dataclass(frozen=True)
class Reference:
    """One face that stands for a person."""

    person_id: uuid.UUID
    embedding: np.ndarray
    aligned: bytes


@dataclass(frozen=True)
class Candidate:
    person_id: uuid.UUID
    score: float
    aligned: bytes


def to_bytes(embedding: np.ndarray) -> bytes:
    return embedding.astype(EMBEDDING_DTYPE).tobytes()


def from_bytes(data: bytes) -> np.ndarray:
    return np.frombuffer(data, dtype=EMBEDDING_DTYPE)


def verdict(score: float) -> Verdict:
    if score >= MATCH_AT:
        return "match"
    return "ask" if score >= ASK_AT else "no"


def candidates(embedding: np.ndarray, references: list[Reference]) -> list[Candidate]:
    """Every person worth considering, closest first, each with their closest reference."""
    if not references:
        return []
    scores = np.stack([r.embedding for r in references]) @ embedding
    best: dict[uuid.UUID, Candidate] = {}
    for reference, raw in zip(references, scores, strict=True):
        score = float(raw)
        current = best.get(reference.person_id)
        if score >= ASK_AT and (current is None or score > current.score):
            best[reference.person_id] = Candidate(reference.person_id, score, reference.aligned)
    return sorted(best.values(), key=lambda c: c.score, reverse=True)


@dataclass
class Track:
    """The same face across the frames of one video."""

    embedding: np.ndarray
    quality: float
    frame_ms: int
    payload: object
    seen: int = 1


def group_frames(
    tracks: list[Track], embedding: np.ndarray, quality: float, frame_ms: int, payload: object
) -> None:
    """Add one face from a video frame to `tracks`, which keep the clearest sighting of each face."""
    if tracks:
        scores = np.stack([t.embedding for t in tracks]) @ embedding
        closest = int(np.argmax(scores))
        if float(scores[closest]) >= MATCH_AT:
            track = tracks[closest]
            track.seen += 1
            if quality > track.quality:
                track.embedding, track.quality, track.frame_ms, track.payload = (
                    embedding,
                    quality,
                    frame_ms,
                    payload,
                )
            return
    tracks.append(Track(embedding, quality, frame_ms, payload))
