# Showcase files moved

The recruiter-showcase notes that used to live in this directory now live in **`portfolio_showcase/`**. Do not put those pipeline posters back here.

Operational architecture (system map, diagrams, decision records) lives beside this file. Start at [overview.md](overview.md).

| What used to live here | Where it is now |
|---|---|
| `riso_retro_flat_v4_*.md`, `lofi_four_stage_pipeline_*.md` | `portfolio_showcase/data/projects/wonder_feed/` |
| `timeline.json` | `portfolio_showcase/data/global_timeline.json` |
| `lofi_four_stage_*.png`, `riso_retro_flat_v4_pipeline_*.png` | `portfolio_showcase/assets/images/wonder_feed/` |
| `_render_four_stage_png.py` | `portfolio_showcase/tools/_render_four_stage_png.py` |
| `portfolio_log.py` | `portfolio_showcase/core/portfolio_log.py` |

See `portfolio_showcase/FRONTEND_BLUEPRINT.md` for the multi-project Next.js contract.

M13 keeps `portfolio_showcase/` at the repository root. It is the home of these posters. It is not moved under `tools/` and it is not retired.
