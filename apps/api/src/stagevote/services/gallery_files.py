"""Turning an uploaded file into the public files of the gallery, and reading its faces.

Nothing here touches the database. A file arrives in INCOMING, which Caddy never serves, and only
becomes public once it has been decoded here. The picture people see and download is what the
uploader sent, with no boxes or marks on it. What changes is only what must:

- A JPEG keeps its exact picture data; only a location in its EXIF is removed, since anyone with the
  gallery link can download it. Other formats are rewritten only if they carry a location.
- HEIC and AVIF (iPhones, newer Android phones) become a high-quality JPEG: most browsers cannot
  show them.
- A video is copied stream by stream into an MP4 or WebM with no metadata (phones record the location
  there too) and its index at the front so playback starts at once. A browser that cannot play it
  (HEVC from an iPhone, say) gets an H.264 copy to watch, and the download stays the full-quality one.

ffmpeg parses files from strangers, so it reads local files only, with a time limit, in a container
with no privileges and a CPU and memory ceiling (compose files).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import piexif
import pillow_heif
from PIL import Image, ImageOps, UnidentifiedImageError

from ..config import get_settings
from ..errors import AppError
from ..logging import get_logger
from .assets import MAX_MEDIA_PIXELS
from .gallery_faces import MIN_SELFIE_FACE_PX, Face, find_faces, rgb_to_bgr
from .gallery_match import Track, group_frames

log = get_logger(__name__)
pillow_heif.register_heif_opener()

INCOMING = ".gallery-incoming"
THUMB_EDGE = 640
ANALYSE_EDGE = 4096
EXIF_GPS_IFD = 0x8825
# Sampling a video: one frame a second, at most this many, at most this large.
FRAME_EDGE = 1920
MAX_FRAMES = 300
# Larger than 8K would not fit in the worker's memory ceiling. There is no limit on a video's length.
MAX_VIDEO_EDGE = 8192
MAX_FACES_PER_VIDEO = 40
# The frames kept for the organisers' analysis view: each one a face was seen clearest in, and a few
# spread over the video, so a video with no faces can be described too.
FRAME_KEEP_EDGE = 1280
SPREAD_FRAMES = 4
FFMPEG_MIN_SECONDS = 1800
# A face seen only once in a video counts only when it is large enough to be someone in the shot.
MIN_PASSING_FACE_PX = 48
_PLAYABLE_MP4 = ({"h264"}, {"yuv420p", "yuvj420p"}, {"aac", "mp3", None})
_PLAYABLE_WEBM = ({"vp8", "vp9"}, {"yuv420p"}, {"opus", "vorbis", None})


class UnreadableFile(Exception):
    """The file is not the picture or video it looked like. Trying again cannot help."""


@dataclass
class Prepared:
    """The public files of one upload (names in the media root) and the faces in it."""

    storage_key: str
    display_key: str
    thumb_key: str
    width: int
    height: int
    duration_ms: int = 0
    faces: list[tuple[Face, int | None]] = field(default_factory=list)
    frames: list[tuple[int, str]] = field(default_factory=list)
    frames_read: int = 1
    faces_seen: int = 0


@dataclass
class Reading:
    """What OpenCV saw in a file, apart from the file's own public copies."""

    faces: list[tuple[Face, int | None]]
    frames: list[tuple[int, str]]
    frames_read: int
    faces_seen: int


@dataclass(frozen=True)
class Selfie:
    face: Face


def root() -> Path:
    return Path(get_settings().media_root)


def incoming_dir() -> Path:
    return root() / INCOMING


def new_key(extension: str) -> str:
    return f"{uuid.uuid4().hex}.{extension}"


def write_public(data: bytes, extension: str) -> str:
    key = new_key(extension)
    temporary = root() / f".{key}.tmp"
    temporary.write_bytes(data)
    os.replace(temporary, root() / key)
    return key


def remove_public(keys: list[str | None]) -> None:
    for key in keys:
        if key and Path(key).name == key:
            (root() / key).unlink(missing_ok=True)


# ---- pictures --------------------------------------------------------------------------


def _open_checked(source: Path | io.BytesIO) -> Image.Image:
    """Open a picture without decoding it, refusing one too large to decode safely."""
    try:
        image = Image.open(source)
        if image.width * image.height > MAX_MEDIA_PIXELS:
            raise UnreadableFile(f"The picture has more than {MAX_MEDIA_PIXELS // 1_000_000} megapixels.")
        return image
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise UnreadableFile("This picture could not be read.") from exc


def _analysis_pixels(image: Image.Image) -> np.ndarray:
    """The upright picture as an array for OpenCV, at most ANALYSE_EDGE on its longest side."""
    if image.format == "JPEG":
        image.draft("RGB", (ANALYSE_EDGE, ANALYSE_EDGE))
    upright = ImageOps.exif_transpose(image) or image
    rgb = upright.convert("RGB")
    rgb.thumbnail((ANALYSE_EDGE, ANALYSE_EDGE), Image.Resampling.LANCZOS)
    return rgb_to_bgr(np.asarray(rgb))


def _has_location(image: Image.Image) -> bool:
    try:
        return bool(image.getexif().get_ifd(EXIF_GPS_IFD))
    except (OSError, ValueError, SyntaxError, AttributeError):
        return False


def _publish_jpeg(source: Path) -> str:
    """A copy of the JPEG without any location. The picture data is not re-encoded."""
    key = new_key("jpg")
    temporary = root() / f".{key}.tmp"
    shutil.copyfile(source, temporary)
    try:
        exif = piexif.load(str(temporary))
        if exif.get("GPS"):
            exif["GPS"] = {}
            piexif.insert(piexif.dump(exif), str(temporary))
    except Exception as exc:  # piexif raises bare ValueErrors and struct errors on damaged EXIF
        with Image.open(source) as image:
            if _has_location(image):
                log.info("location removed by re-encoding", extra={"error": type(exc).__name__})
                exif_data = image.getexif()
                exif_data.get_ifd(EXIF_GPS_IFD).clear()
                del exif_data[EXIF_GPS_IFD]
                image.save(temporary, format="JPEG", quality=95, subsampling="keep", exif=exif_data)
    os.replace(temporary, root() / key)
    return key


def _publish_picture(source: Path, image: Image.Image) -> str:
    if image.format == "JPEG":
        return _publish_jpeg(source)
    extension = {"PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(image.format or "")
    if extension is None:  # HEIC, AVIF: a JPEG with the picture turned upright and no metadata
        upright = ImageOps.exif_transpose(image) or image
        out = io.BytesIO()
        upright.convert("RGB").save(out, format="JPEG", quality=92, subsampling=0, optimize=True)
        return write_public(out.getvalue(), "jpg")
    if extension != "gif" and _has_location(image):
        out = io.BytesIO()
        options = {"lossless": True} if extension == "webp" else {}
        image.save(out, format=image.format, **options)
        return write_public(out.getvalue(), extension)
    key = new_key(extension)
    temporary = root() / f".{key}.tmp"
    shutil.copyfile(source, temporary)
    os.replace(temporary, root() / key)
    return key


def _thumbnail(image: Image.Image) -> bytes:
    small = image.convert("RGB")
    small.thumbnail((THUMB_EDGE, THUMB_EDGE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    small.save(out, format="WEBP", quality=80, method=4)
    return out.getvalue()


def prepare_image(source: Path) -> Prepared:
    written: list[str | None] = []
    try:
        with _open_checked(source) as image:
            try:
                storage = _publish_picture(source, image)
                written.append(storage)
                pixels = _analysis_pixels(image)
            except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
                raise UnreadableFile("This picture could not be read.") from exc
        faces: list[tuple[Face, int | None]] = [(face, None) for face in find_faces(pixels)]
        with _open_checked(root() / storage) as public:
            upright = ImageOps.exif_transpose(public) or public
            width, height = upright.width, upright.height
            thumb = write_public(_thumbnail(upright), "webp")
        written.append(thumb)
        return Prepared(
            storage_key=storage,
            display_key=storage,
            thumb_key=thumb,
            width=width,
            height=height,
            faces=faces,
            faces_seen=len(faces),
        )
    except Exception:
        remove_public(written)
        raise


def read_selfie(data: bytes) -> Selfie:
    """The face to remember from a selfie: the largest one in the picture."""
    try:
        with _open_checked(io.BytesIO(data)) as image:
            pixels = _analysis_pixels(image)
    except (UnreadableFile, OSError, ValueError, SyntaxError) as exc:
        raise AppError(
            "INVALID_IMAGE", "That file could not be read as a photo. Try another one.", 422
        ) from exc
    faces = find_faces(pixels, min_px=1)
    if not faces:
        raise AppError(
            "NO_FACE",
            "We could not find a face in that photo. Face the camera in good light and try again.",
            422,
        )
    if faces[0].size_px < MIN_SELFIE_FACE_PX:
        raise AppError(
            "FACE_TOO_SMALL", "Your face is too small in that photo. Move closer and try again.", 422
        )
    return Selfie(faces[0])


# ---- videos ----------------------------------------------------------------------------


def _ffmpeg(*arguments: str, timeout: float) -> None:
    command = [
        "nice",
        "-n",
        "10",
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-protocol_whitelist",
        "file",
        *arguments,
    ]
    subprocess.run(command, check=True, capture_output=True, timeout=timeout)  # noqa: S603 - fixed program, no shell


def probe(source: Path) -> dict[str, object]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-protocol_whitelist",
        "file",
        "-show_streams",
        "-show_format",
        "-of",
        "json",
        str(source),
    ]
    try:
        result = subprocess.run(command, check=True, capture_output=True, timeout=120)  # noqa: S603
        answer = json.loads(result.stdout)
    except (subprocess.SubprocessError, ValueError) as exc:
        raise UnreadableFile("This video could not be read.") from exc
    return answer if isinstance(answer, dict) else {}


def _rotated(stream: dict[str, object]) -> bool:
    entries = stream.get("side_data_list")
    for entry in entries if isinstance(entries, list) else []:
        rotation = entry.get("rotation") if isinstance(entry, dict) else None
        if isinstance(rotation, (int, float)) and abs(int(rotation)) % 180 == 90:
            return True
    return False


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class VideoInfo:
    width: int
    height: int
    seconds: float
    codec: str
    pixel_format: str
    audio: str | None


def video_info(source: Path) -> VideoInfo:
    answer = probe(source)
    streams = answer.get("streams")
    streams = [s for s in streams if isinstance(s, dict)] if isinstance(streams, list) else []
    video = next(
        (s for s in streams if s.get("codec_type") == "video" and s.get("codec_name") != "mjpeg"), None
    )
    if video is None:
        raise UnreadableFile("This file has no video in it.")
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    width, height = int(_number(video.get("width"))), int(_number(video.get("height")))
    if width <= 0 or height <= 0 or max(width, height) > MAX_VIDEO_EDGE:
        raise UnreadableFile("This video's picture size is not supported.")
    form = answer.get("format")
    seconds = _number(form.get("duration")) if isinstance(form, dict) else 0.0
    seconds = seconds or _number(video.get("duration"))
    if _rotated(video):
        width, height = height, width
    return VideoInfo(
        width=width,
        height=height,
        seconds=seconds,
        codec=str(video.get("codec_name") or ""),
        pixel_format=str(video.get("pix_fmt") or ""),
        audio=str(audio.get("codec_name")) if audio else None,
    )


def _playable_as(info: VideoInfo) -> str | None:
    """The file type browsers can play this video's streams in, without converting them."""
    for extension, (videos, pixels, audios) in (("mp4", _PLAYABLE_MP4), ("webm", _PLAYABLE_WEBM)):
        if info.codec in videos and info.pixel_format in pixels and info.audio in audios:
            return extension
    return None


def _time_limit(info: VideoInfo) -> float:
    return max(FFMPEG_MIN_SECONDS, info.seconds * 8)


def _copy_streams(source: Path, info: VideoInfo, extension: str) -> str | None:
    """The video's own streams in a new file with no metadata; None if they do not fit one."""
    key = new_key(extension)
    temporary = root() / f".{key}.tmp"
    tag = ["-tag:v", "hvc1"] if info.codec == "hevc" and extension == "mp4" else []
    common = [
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c:v",
        "copy",
        *tag,
    ]
    attempts = (
        [["-c:a", "copy"], ["-c:a", "aac", "-b:a", "192k"]] if extension == "mp4" else [["-c:a", "copy"]]
    )
    for audio in attempts:
        container = ["-movflags", "+faststart", "-f", "mp4"] if extension == "mp4" else ["-f", "webm"]
        try:
            _ffmpeg("-i", str(source), *common, *audio, *container, str(temporary), timeout=_time_limit(info))
        except (subprocess.SubprocessError, OSError):
            temporary.unlink(missing_ok=True)
            continue
        os.replace(temporary, root() / key)
        return key
    return None


def _transcode(source: Path, info: VideoInfo) -> str:
    """An H.264 copy every browser plays, at most 1080p."""
    key = new_key("mp4")
    temporary = root() / f".{key}.tmp"
    threads = str(max(1, get_settings().gallery_ffmpeg_threads))
    scale = "scale='if(gte(iw,ih),min(1920,iw),-2)':'if(gte(iw,ih),-2,min(1920,ih))'"
    try:
        _ffmpeg(
            "-threads", threads, "-i", str(source), "-map", "0:v:0", "-map", "0:a:0?", "-map_metadata", "-1",
            "-map_chapters", "-1", "-vf", f"{scale},format=yuv420p", "-c:v", "libx264", "-preset", "veryfast",
            "-crf", "23", "-threads", threads, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
            "-f", "mp4", str(temporary), timeout=_time_limit(info),
        )  # fmt: skip
    except (subprocess.SubprocessError, OSError) as exc:
        temporary.unlink(missing_ok=True)
        raise UnreadableFile("This video could not be converted.") from exc
    os.replace(temporary, root() / key)
    return key


def _poster(source: Path, info: VideoInfo) -> str:
    moment = f"{min(1.0, info.seconds / 2):.3f}"
    with tempfile.TemporaryDirectory(prefix="gallery-poster-") as folder:
        frame = Path(folder) / "poster.jpg"
        try:
            _ffmpeg("-ss", moment, "-i", str(source), "-frames:v", "1", "-q:v", "3", str(frame), timeout=300)
        except (subprocess.SubprocessError, OSError) as exc:
            raise UnreadableFile("This video could not be read.") from exc
        with _open_checked(frame) as image:
            return write_public(_thumbnail(image), "webp")


def _frames(source: Path, info: VideoInfo, folder: Path) -> Iterator[tuple[Path, int]]:
    rate = 1.0 if info.seconds <= MAX_FRAMES else MAX_FRAMES / info.seconds
    scale = f"scale='if(gte(iw,ih),min({FRAME_EDGE},iw),-2)':'if(gte(iw,ih),-2,min({FRAME_EDGE},ih))'"
    threads = str(max(1, get_settings().gallery_ffmpeg_threads))
    pattern = str(folder / "%05d.jpg")
    try:
        _ffmpeg(
            "-threads", threads, "-i", str(source), "-map", "0:v:0", "-vf", f"fps={rate:.6f},{scale}",
            "-frames:v", str(MAX_FRAMES), "-q:v", "3", pattern, timeout=_time_limit(info),
        )  # fmt: skip
    except (subprocess.SubprocessError, OSError) as exc:
        raise UnreadableFile("This video could not be read.") from exc
    for index, frame in enumerate(sorted(folder.glob("*.jpg"))):
        yield frame, round(index / rate * 1000)


def _spread(moments: list[int]) -> set[int]:
    if len(moments) <= SPREAD_FRAMES:
        return set(moments)
    return {moments[round(i * (len(moments) - 1) / (SPREAD_FRAMES - 1))] for i in range(SPREAD_FRAMES)}


def _frame_picture(image: Image.Image) -> bytes:
    small = image.convert("RGB")
    small.thumbnail((FRAME_KEEP_EDGE, FRAME_KEEP_EDGE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    small.save(out, format="WEBP", quality=80, method=4)
    return out.getvalue()


def read_video(source: Path, info: VideoInfo) -> Reading:
    """The faces in a video, each with its clearest sighting, and the frames worth keeping."""
    tracks: list[Track] = []
    seen = 0
    with tempfile.TemporaryDirectory(prefix="gallery-frames-") as name:
        paths: dict[int, Path] = {}
        for path, moment in _frames(source, info, Path(name)):
            paths[moment] = path
            with _open_checked(path) as image:
                pixels = _analysis_pixels(image)
            for face in find_faces(pixels):
                seen += 1
                group_frames(tracks, face.embedding, face.quality, moment, face)
        total = len(paths)
        kept = [t for t in tracks if t.seen > 1 or total < 3 or _face(t).size_px >= MIN_PASSING_FACE_PX]
        kept.sort(key=lambda t: (t.seen, t.quality), reverse=True)
        kept = kept[:MAX_FACES_PER_VIDEO]
        moments = {t.frame_ms for t in kept} | _spread(sorted(paths))
        frames: list[tuple[int, str]] = []
        try:
            for moment in sorted(moments):
                with _open_checked(paths[moment]) as image:
                    frames.append((moment, write_public(_frame_picture(image), "webp")))
        except Exception:
            remove_public([key for _, key in frames])
            raise
    return Reading([(_face(t), t.frame_ms) for t in kept], frames, total, seen)


def read_again(kind: str, public: Path) -> Reading:
    """What OpenCV sees in a file already public, for one read before the analysis view existed."""
    if kind == "video":
        return read_video(public, video_info(public))
    with _open_checked(public) as image:
        faces = find_faces(_analysis_pixels(image))
    return Reading([(face, None) for face in faces], [], 1, len(faces))


def _face(track: Track) -> Face:
    face = track.payload
    assert isinstance(face, Face)
    return face


def prepare_video(source: Path) -> Prepared:
    info = video_info(source)
    playable = _playable_as(info)
    written: list[str | None] = []
    try:
        storage = _copy_streams(source, info, "webm" if playable == "webm" else "mp4")
        written.append(storage)
        if playable and storage:
            display = storage
        else:
            display = _transcode(root() / storage if storage else source, info)
            written.append(display)
        storage = storage or display
        thumb = _poster(root() / display, info)
        written.append(thumb)
        reading = read_video(root() / display, info)
        written += [key for _, key in reading.frames]
        return Prepared(
            storage_key=storage,
            display_key=display,
            thumb_key=thumb,
            width=info.width,
            height=info.height,
            duration_ms=round(info.seconds * 1000),
            faces=reading.faces,
            frames=reading.frames,
            frames_read=reading.frames_read,
            faces_seen=reading.faces_seen,
        )
    except Exception:
        remove_public(written)
        raise
