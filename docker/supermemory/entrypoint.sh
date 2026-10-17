#!/bin/sh
# Map the Gemini key Omni-Engine already stores. Never print either key.
set -eu
if [ -z "${GEMINI_API_KEY:-}" ] && [ -n "${GOOGLE_API_KEY:-}" ]; then
  GEMINI_API_KEY=$GOOGLE_API_KEY
  export GEMINI_API_KEY
fi
exec /usr/local/bin/supermemory-server
