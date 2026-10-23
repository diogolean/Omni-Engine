#!/usr/bin/env bash
# Ubuntu apt mirrors sometimes stall. Bound each attempt and retry so a
# hung `apt-get install ffmpeg` cannot sit for half an hour.
set -euo pipefail

if command -v ffmpeg >/dev/null 2>&1 && command -v ffprobe >/dev/null 2>&1; then
  ffmpeg -hide_banner -version | head -n 1
  exit 0
fi

attempt=1
while [ "$attempt" -le 3 ]; do
  echo "apt ffmpeg attempt ${attempt}/3"
  if sudo timeout 90 apt-get update \
    && sudo timeout 120 apt-get install -y ffmpeg libass9; then
    ffmpeg -hide_banner -version | head -n 1
    exit 0
  fi
  echo "apt ffmpeg attempt ${attempt} failed" >&2
  sleep $((attempt * 5))
  attempt=$((attempt + 1))
done

echo "ffmpeg install failed after 3 attempts" >&2
exit 1
