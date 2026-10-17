# Omni-Engine runtime for the Oracle VM (Ubuntu 24.04, linux/arm64).
# The image is arch-agnostic; GitHub Actions builds linux/arm64 with buildx.
# mediapipe has no aarch64 wheel and is omitted (see requirements-docker.txt).

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/diogolean/Omni-Engine" \
      org.opencontainers.image.title="omni-engine" \
      org.opencontainers.image.description="Omni-Engine content factory and Pinterest drip scheduler"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONUTF8=1 \
    OMNI_ROOT=/opt/omni-engine

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        fonts-liberation \
        fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/omni-engine

COPY requirements-docker.txt ./
RUN pip install --no-cache-dir -r requirements-docker.txt

COPY . .

RUN mkdir -p /opt/omni-engine/outputs /opt/omni-engine/credentials

CMD ["python", "-m", "omni.worker", "--role", "scheduler"]
