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

`python scripts/backup_supermemory.py` still runs inside the engine container. The Supermemory backup is not scheduled. No cron on the VM calls `backup_supermemory.sh`. Deploy copies `deploy/vm/backup_supermemory.sh` to `/opt/omni-engine/scripts/backup_supermemory.sh` and does not run it. Until scheduling is decided, run that shell script by hand on the VM. It archives the volume, then calls this Python shim.

See [tools/oneoff/README.md](../tools/oneoff/README.md) and [tools/lofi/README.md](../tools/lofi/README.md).
