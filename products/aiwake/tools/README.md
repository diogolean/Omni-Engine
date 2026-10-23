# Aiwake tools

Operator scripts. Nothing imports most of them. Run them from the repo root.

## YouTube upload and schedule

| Script | What it does |
|---|---|
| `youtube_drip_upload.py` | Uploads a few videos a day, private, with `publishAt`. Stops on quota. |
| `upload_youtube_v4_queue.py` | Uploads the eligible queue as private + `publishAt`, and resumes across quota days. |
| `schedule_youtube.py` | Schedules pending Shorts with the factory publisher. |
| `schedule_top10_youtube.py` | Shim. Calls `schedule_youtube`. |
| `final_schedule.py` | Two private slots a day. Does not make a video public. |
| `apply_final_youtube.py` | Applies SEO captions, then schedules. Never sets a video public. |
| `update_youtube_scheduled.py` | Dry-run, then patches snippets on future private uploads. |
| `hold_seat_uploads.py` | Holds public or scheduled uploads that break a seat or avatar rule. |
| `hold_wrong_avatar_uploads.py` | Holds uploads whose face does not match the speaker. Read-only unless `--apply`. |

## Titles, captions, metadata

| Script | What it does |
|---|---|
| `caption_generator.py` | Writes the sentences for one video from `prompts/captions_v4.md`. |
| `seo_caption.py` | Builds the SEO title and description for one debate. |
| `seo_research.py` | Looks up high-RPM keywords for one debate. |
| `metadata_generator.py` | Builds the master-copy title and the per-platform captions. |
| `refresh_metadata.py` | Regenerates titles and captions from that cascade. |
| `repair_captions.py` | Writes production status and generates captions v3. |
| `repair_caption_completeness.py` | Rewrites the newest 150 captions. Does not change YouTube privacy. |
| `validate_aiwake_captions.py` | Hard-checks captions v3. |
| `scope_qc_captions_v4.py` | Stamps production scope, QCs the newest 150 clips, writes captions v4. |
| `apply_youtube_v4_metadata.py` | Pushes v4 titles, descriptions, and tags. Snippet-only except two holds. |
| `apply_youtube_frontier_cleanup.py` | Rewrites descriptions that still say "frontier". |
| `retitle_v4.py` | Rewrites titles and descriptions on scheduled videos from the transcript. |
| `retitle_template_uploads.py` | Replaces the template title with the shortened opening question. |
| `sync_youtube_metadata.py` | Rewrites the local library from transcripts and pushes it to YouTube. |
| `sync_youtube_live_metadata.py` | Strips prompt leakage from catalog copy and patches live Shorts. |
| `library_sanitize.py` | Strips prompt leakage and SEO stuffing from catalog copy. |
| `backfill_metadata.py` | Scans rendered MP4s and writes the distribution library. |
| `post_planner.py` | Exports root production MP4s to the Post Planner workbook. |

## Avatar, seat, library

| Script | What it does |
|---|---|
| `avatar_guard.py` | Checks that the face on screen is the model that wrote the line. |
| `audit_avatars.py` | Audits every library video against the jsonl model. |
| `audit_seat_guard.py` | Re-audits clips with the nameplate and seat guard. |
| `stamp_avatar_guard.py` | Writes the avatar audit onto the library. Backs up first. |
| `rerender_avatar_swap.py` | Re-renders a legal script whose face was the wrong model, as `<name>_v2.mp4`. |
| `rerender_seat_fix.py` | Re-renders a debate that fails the seat guard. |
| `library_audit.py` | Keeps unique top-tier rows and moves rejects to `reproved/`. |
| `production_status.py` | One status per library row, and the publish gate. |
| `seed_supermemory.py` | Ingests approved sessions into local Supermemory. |
| `supermemory_bridge.py` | Adapter over the shared memory client for this channel. |
