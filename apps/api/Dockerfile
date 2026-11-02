# StageVote API and worker image (the worker runs `stagevote worker`).
FROM python:3.12-slim AS build
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never PIP_DISABLE_PIP_VERSION_CHECK=1
RUN pip install --no-cache-dir "uv==0.12.5"
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime
# DejaVu is used only to draw the opt-in demo placeholder artwork. ffmpeg reads and converts the
# photo gallery's videos.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin stagevote \
    && mkdir -p /data/media \
    && chown stagevote:stagevote /data/media
WORKDIR /app
COPY --from=build --chown=root:root /app /app
# OpenCV's face detector (YuNet, MIT) and recogniser (SFace, Apache 2.0) for the photo gallery.
ADD --checksum=sha256:8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 \
    https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
    /app/models/face_detection_yunet_2023mar.onnx
ADD --checksum=sha256:0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 \
    https://github.com/opencv/opencv_zoo/raw/48b10fa505aeb51fbd127fc60a5c660040e823b7/models/face_recognition_sface/face_recognition_sface_2021dec.onnx \
    /app/models/face_recognition_sface_2021dec.onnx
RUN chmod -R a+rX /app/models
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 MEDIA_ROOT=/data/media
USER stagevote
EXPOSE 8000
CMD ["uvicorn", "stagevote.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
