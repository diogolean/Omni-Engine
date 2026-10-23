# linux/arm64 supermemory-server for the Oracle VM.
# Pin 0.0.9 or newer. 0.0.8 and older bind every interface without auth.
# The checksum is the published linux-arm64 digest for this exact release.

FROM --platform=linux/arm64 debian:bookworm-slim

ARG SUPERMEMORY_VERSION=0.0.9
ARG SUPERMEMORY_SHA256=b789be16cd7192b3c08e882396c0a94ba330ea467d5a7fb1b39ac3684abf5374

LABEL org.opencontainers.image.source="https://github.com/diogolean/Omni-Engine" \
      org.opencontainers.image.title="supermemory-server" \
      org.opencontainers.image.version="${SUPERMEMORY_VERSION}" \
      org.opencontainers.image.description="Self-hosted Supermemory API v3 for Omni-Engine"

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --home /data --shell /usr/sbin/nologin supermemory \
    && mkdir -p /data \
    && chown supermemory:supermemory /data

RUN curl -fsSL -o /tmp/supermemory-server \
      "https://github.com/supermemoryai/supermemory/releases/download/server-v${SUPERMEMORY_VERSION}/supermemory-server-linux-arm64" \
    && echo "${SUPERMEMORY_SHA256}  /tmp/supermemory-server" | sha256sum -c - \
    && install -m 0755 /tmp/supermemory-server /usr/local/bin/supermemory-server \
    && rm -f /tmp/supermemory-server

COPY entrypoint.sh /usr/local/bin/supermemory-entrypoint
RUN chmod 0755 /usr/local/bin/supermemory-entrypoint

ENV SUPERMEMORY_DATA_DIR=/data \
    SUPERMEMORY_HOST=0.0.0.0 \
    SUPERMEMORY_PORT=6767 \
    SUPERMEMORY_DISABLE_TELEMETRY=1

EXPOSE 6767
VOLUME /data
USER supermemory
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=5 \
    CMD curl -fsS http://127.0.0.1:6767/v3/health || exit 1
ENTRYPOINT ["/usr/local/bin/supermemory-entrypoint"]
