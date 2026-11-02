"""Finding and describing faces with OpenCV: the YuNet detector and the SFace recogniser.

Both models are small ONNX files built into the image (apps/api/Dockerfile). They run on the CPU,
with no network. Importing this module loads OpenCV, so only what needs it imports it.
"""

from __future__ import annotations

import io
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..config import get_settings
from ..errors import AppError

DETECTOR_FILE = "face_detection_yunet_2023mar.onnx"
RECOGNISER_FILE = "face_recognition_sface_2021dec.onnx"
DETECT_SCORE = 0.8
# Faces smaller than this say too little about who they are.
MIN_FACE_PX = 24
MIN_SELFIE_FACE_PX = 60
# The detector finds faces of about 10 to 300 pixels in what it is given, so each picture is looked
# at in several sizes. A selfie's face fills the small size; a group's small faces need the large
# ones (a 1280-pixel group photo from a chat app has faces about 40 pixels wide).
DETECT_EDGES = (640, 1600, 2560)
DUPLICATE_OVERLAP = 0.35
MAX_FACES = 200
AVATAR_PX = 256
FULL_QUALITY_PX = 160


@dataclass(frozen=True)
class Face:
    embedding: np.ndarray
    # The face turned upright and cut to 112 pixels, as JPEG: what the vision model compares.
    aligned: bytes
    # The face with a margin, as WebP: the round picture on the People page.
    crop: bytes
    size_px: int
    quality: float
    # Left, top, width and height as shares of the picture, for the organisers' analysis view.
    box: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    detector_score: float = 0.0


# The models keep state while they run, so each thread has its own (about 45 MB). Callers use small fixed
# thread pools, which is also what bounds their number.
_local = threading.local()


def _load() -> tuple[cv2.FaceDetectorYN, cv2.FaceRecognizerSF]:
    models = getattr(_local, "models", None)
    if models is None:
        folder = Path(get_settings().gallery_models_dir)
        try:
            detector = cv2.FaceDetectorYN.create(
                str(folder / DETECTOR_FILE), "", (320, 320), DETECT_SCORE, 0.3, 5000
            )
            recogniser = cv2.FaceRecognizerSF.create(str(folder / RECOGNISER_FILE), "")
        except cv2.error as exc:
            raise AppError(
                "GALLERY_UNAVAILABLE", "Face matching is not available right now. Try again later.", 503
            ) from exc
        cv2.setNumThreads(max(1, get_settings().gallery_cv_threads))
        models = (detector, recogniser)
        _local.models = models
    return models


def _crop(bgr: np.ndarray, box: np.ndarray) -> bytes:
    """A square around the face with room for hair and chin, on a plain edge if it runs out."""
    height, width = bgr.shape[:2]
    x, y, w, h = (float(v) for v in box[:4])
    side = max(w, h) * 1.7
    centre_x, centre_y = x + w / 2, y + h / 2 - h * 0.04
    half = side / 2
    left, top = round(centre_x - half), round(centre_y - half)
    right, bottom = round(centre_x + half), round(centre_y + half)
    pad = max(0, -left, -top, right - width, bottom - height)
    if pad:
        bgr = cv2.copyMakeBorder(bgr, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        left, top, right, bottom = left + pad, top + pad, right + pad, bottom + pad
    square = bgr[top:bottom, left:right]
    interpolation = cv2.INTER_AREA if square.shape[0] > AVATAR_PX else cv2.INTER_CUBIC
    small = cv2.resize(square, (AVATAR_PX, AVATAR_PX), interpolation=interpolation)
    out = io.BytesIO()
    Image.fromarray(cv2.cvtColor(small, cv2.COLOR_BGR2RGB)).save(out, format="WEBP", quality=84, method=4)
    return out.getvalue()


def _scales(longest: int) -> list[float]:
    scales = [edge / longest for edge in DETECT_EDGES if edge <= max(longest, DETECT_EDGES[1])]
    return scales or [1.0]


def _overlap(first: np.ndarray, second: np.ndarray) -> float:
    left, top = max(first[0], second[0]), max(first[1], second[1])
    right = min(first[0] + first[2], second[0] + second[2])
    bottom = min(first[1] + first[3], second[1] + second[3])
    shared = max(0.0, float(right - left)) * max(0.0, float(bottom - top))
    return shared / (float(first[2] * first[3]) + float(second[2] * second[3]) - shared + 1e-6)


def _detect(detector: cv2.FaceDetectorYN, bgr: np.ndarray) -> list[np.ndarray]:
    """Every face in every size, in the picture's own pixels, each one once."""
    found: list[np.ndarray] = []
    longest = max(bgr.shape[:2])
    for scale in _scales(longest):
        interpolation = cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC
        resized = (
            bgr if scale == 1 else cv2.resize(bgr, None, fx=scale, fy=scale, interpolation=interpolation)
        )
        detector.setInputSize((resized.shape[1], resized.shape[0]))
        _, rows = detector.detect(resized)
        for row in rows if rows is not None else []:
            face = row.copy()
            face[:14] /= scale  # the box and the five landmarks
            found.append(face)
    kept: list[np.ndarray] = []
    for face in sorted(found, key=lambda row: float(row[14]), reverse=True):
        if all(_overlap(face, other) < DUPLICATE_OVERLAP for other in kept):
            kept.append(face)
    return kept


def _share_of_picture(box: np.ndarray, height: int, width: int) -> tuple[float, float, float, float]:
    left, top = max(0.0, float(box[0])), max(0.0, float(box[1]))
    right, bottom = min(float(width), float(box[0] + box[2])), min(float(height), float(box[1] + box[3]))
    return (
        round(left / width, 4),
        round(top / height, 4),
        round(max(0.0, right - left) / width, 4),
        round(max(0.0, bottom - top) / height, 4),
    )


def find_faces(bgr: np.ndarray, *, min_px: int = MIN_FACE_PX) -> list[Face]:
    """Every clear face in a picture, largest first. `bgr` is left unchanged."""
    if max(bgr.shape[:2]) == 0:
        return []
    height, width = bgr.shape[:2]
    detector, recogniser = _load()
    rows = sorted(_detect(detector, bgr), key=lambda row: float(row[2] * row[3]), reverse=True)
    faces: list[Face] = []
    for box in rows[:MAX_FACES]:
        size = round(float(min(box[2], box[3])))
        if size < min_px:
            continue
        aligned = recogniser.alignCrop(bgr, box)
        feature = recogniser.feature(aligned).flatten().astype(np.float32)
        norm = float(np.linalg.norm(feature))
        if norm == 0:
            continue
        ok, jpeg = cv2.imencode(".jpg", aligned, [cv2.IMWRITE_JPEG_QUALITY, 92])
        if not ok:
            continue
        faces.append(
            Face(
                embedding=feature / norm,
                aligned=jpeg.tobytes(),
                crop=_crop(bgr, box),
                size_px=size,
                quality=float(box[14]) * min(1.0, size / FULL_QUALITY_PX),
                box=_share_of_picture(box, height, width),
                detector_score=round(float(box[14]), 3),
            )
        )
    return faces


def rgb_to_bgr(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
