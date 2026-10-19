# Tools

Operator scripts. Run one from the repo root. Nothing in the factory imports them as a library.

| Script | What it does |
|---|---|
| `audit_google_costs.py` | Reads local Google and Gemini spend. |
| `backfill_metadata.py` | Audits metadata for every channel under `channels_config/`. |
| `cleanup_ephemeral.py` | Deletes ephemeral build artifacts. |
| `migrate_local_outputs_to_factory.py` | One-time move of a local `outputs/<channel>/` tree onto `OUTPUT_PATH`. |
| `repair_doubled_output_paths.py` | Strips a doubled `OUTPUT_PATH` prefix. |
| `restore_channel_state.py` | Restores missing `channels_config/<channel>/store/` files. |
| `validate_aiwake_captions.py` | Entry point for the Aiwake caption validator. |
