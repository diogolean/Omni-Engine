# Scripts

Shims until M14. The files live under `tools/`.

| Here | Implementation |
|---|---|
| `scripts/backup_supermemory.py` | `tools/backup_supermemory.py` |
| `scripts/<name>.py` | `tools/oneoff/<name>.py` |
| `scripts/checks/` | `tools/oneoff/checks/` |
| `scripts/lofi/` | `tools/lofi/` |
| `scripts/build_post_planner.py` | `products/aiwake/tools/post_planner.py` |
| `scripts/sync_youtube_live_metadata.py` | `products/aiwake/tools/sync_youtube_live_metadata.py` |

`python scripts/backup_supermemory.py` still runs inside the engine container. The VM cron calls `/opt/omni-engine/scripts/backup_supermemory.sh`, which is the shell script copied from `deploy/vm/`, and that shell script calls this Python shim.

See [tools/oneoff/README.md](../tools/oneoff/README.md) and [tools/lofi/README.md](../tools/lofi/README.md).
