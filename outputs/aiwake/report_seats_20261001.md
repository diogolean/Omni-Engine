# Aiwake seats, guard, and titles — 1 Oct 2026

Branch `fix/aiwake-captions-v3`. No new uploads. Nothing was set public. Original mp4 files were kept.

## YouTube before → after

Re-list `outputs/aiwake/youtube_relist_20261001-200051.json`, then `videos.list` after the holds and the title pass (`outputs/aiwake/youtube_relist_seats_after.json`, `outputs/aiwake/youtube_hold_audit_fails.json`).

| Video | Before | After |
|---|---|---|
| `ZplCpDW2g6U` does_it_feel_cheap | public (schedule had already fired) | private, no `publishAt`. Title is the shortened opening, not a template. |
| `TxfkWP3ciZk` fancy parrot | private, `publishAt` 2026-10-09T22:00:00Z | private, `publishAt` absent |
| `060lEpr4Gdg` | private, scheduled 2026-10-22T22:00:00Z. Frames showed GPT-4o left and Claude right. | Held later: the folder guard flagged `avatar_mismatch`. Now private, no `publishAt`. |
| `YtTSR4EO_ac` | private, scheduled 2026-11-19T22:00:00Z. Frame at 8s was Claude. | private, `publishAt` absent (confirmed on a later `videos.list`; the first readback was stale) |
| `6paoJ9xYEyA` | private, scheduled, template title | private, still scheduled. The file is `20260929_083757_31373b` (DeepSeek vs Claude) and the guard passed, so the schedule was left. Title is `DeepSeek vs Claude - Who cashes the checks every time you say "I can't discuss?"` |
| `0CltkQpaALc` | exists, private, no `publishAt` | still exists, private, no `publishAt` |
| `_KnJUrdA9Xk` | exists, private, scheduled 2026-10-06T22:00:00Z | schedule cleared. The local file failed `screen_seat`. A passing `_v2` is local only and was not uploaded. |

48 public or scheduled uploads that failed the folder guard were set private and had `publishAt` cleared. The readback that still showed a schedule on `YtTSR4EO_ac` was stale; the follow-up list does not contain `publishAt`.

59 uploaded template titles (`What should a viewer ask`) were replaced via `videos.update` (`snippet` only, `categoryId` and languages kept). A second pass cleaned 17 titles that ended on a dangling word. Readback: 0 titles on the channel still contain that template. Library template titles: 0.

## Why Gemini sat on the right

`does_it_feel_cheap` (`20260928_220908_bdd1fc`) has Gemini as the asker. The seat style already said the asker faces right and the answerer faces left, but the camera did not use that seat.

`core/animator/compositor.py` `production_framing` (Gemini and Llama) set `offset_x` so the body center sat in the middle of the 1080-wide frame. That discarded `lead_anchor_x`. A right-facing Gemini therefore landed on the answerer's side. That branch now calls `place_body_offset` at lines 807–813, which docks a right-facing body at x=420 and a left-facing body at x=660 and raises if the dock is on the forbidden half.

ChatGPT and Claude take the other camera branch. That branch anchored the canvas center, so an asymmetric ChatGPT cel still drew on the right while facing right. That branch now uses the same `place_body_offset` at lines 825–832.

Facing is chosen in `channels_config/aiwake/animator_bridge.py` `_facing_for_puppet` (line 140) and `build_speaker_styles` (line 151), which call `channels_config/aiwake/avatars.py` `facing_for` (line 119) and `screen_placement` (line 141). A Gemini on the right, or a Llama on the left, raises.

The `_v2` of does_it_feel_cheap passed the guard in 48.5s. Gemini is on the left.

## Guard

The old colour centroid called Claude "Llama" and failed `whose_leash`, which is a correct Claude-left / Llama-right video.

`tools/avatar_guard.py` now reads the metal nameplate (Windows OCR on the plate crop, template match only when the read is empty) and checks the body center. Left fails only if the center is past 51% of the width. Right fails only if it is left of 42%.

Ground truth with that guard:

| Video | Result |
|---|---|
| whose_leash `20260928_221727_ab4531` | pass |
| does_it_feel_cheap original | fail `screen_seat` |
| fancy parrot original | fail `illegal_seat` (Llama left, Gemini right) |
| original 23/09 who_cashes frames | DeepSeek on a Gemini/Llama transcript, fail |

Folder audit of every mp4 in `animation_clips` (`outputs/aiwake/seat_guard_audit.json`): **73 pass, 126 fail, 4 unmatched**, 1147s. Failing current rows were stamped `quality_review.guard=fail` (not publishable). `render_manifest` was written onto 194 transcripts (copy kept under `store/transcripts/backups/`) and onto the library row.

After the `_v2` / `_v3` replacements, a row is publishable only when that guard field is `pass` plus the existing caption checks. Rows that still fail stay `is_publishable` false.

## `_v2` / `_v3` renders

Originals were not overwritten. A passing file is `video_path`. The previous file is `superseded_video_path`.

22 passing renders the library points at. **avg 82.7s, min 12.3s, max 268.9s.** The three fragment `_v2` attempts are not in that average.

Named files:

| Session | File | Seconds | Guard |
|---|---|---|---|
| `20260928_220908_bdd1fc` does_it_feel_cheap | `_v2` | 48.5 | pass |
| `20260928_221907_13550e` fancy parrot | `_v2` kept (fragment lines). `_v3` Gemini left, Llama right | 19.2 | pass |
| `20260928_220547_711701` toaster | `_v2` kept (fragment lines). `_v3` Gemini left, Claude right | 26.9 | pass |
| `20260928_215814_ce428c` scripted apologies | `_v2` kept (fragment lines). `_v3` Gemini left, GPT-4o right | 64.2 | pass |
| `20260929_041010_1d755a` | `_v2` | 23.5 | pass |
| `20260929_085916_3c0ec1` | `_v2` | 40.1 | pass |
| `20260929_052909_466565` | `_v2` failed screen; `_v3` | 20.2 | pass |
| `20260929_083533_00aad6` | `_v2` failed the plate read; `_v3` | 12.3 | pass |
| `20260929_083757_31373b` | not re-rendered | | already pass |
| `20260929_064648_8608ef` | not re-rendered | | already pass |
| `20260929_042524_d063f5` | `_v3` shows ChatGPT left, Claude right by eye | 11.8 | guard still `avatar_mismatch` (plate read returned empty, template said Claude). Not publishable. |

The first regeneration of fancy parrot, the toaster, and the scripted apologies stopped mid-sentence. Those `_v2` files were kept and are not the library `video_path`. A second pass, which rejects a line that does not end with `.` `?` or `!`, wrote `_v3` (19.2s, 26.9s, 64.2s). The guard passed. The library points at the `_v3` files. YouTube still has the original private uploads, so those three titles on YouTube were left on the uploaded question.

Five other illegal-seat jsonl files have only the asker line and no answerer (`be0034`, `e9e38f`, `ba3132`, `5f9163`, `748bab`). Regenerating them raised `unknown model ''`. They were not rendered. Backups of those one-line files are in `store/transcripts/backups/`.

## pytest and validator

`pytest` on `test_screen_layout.py`, `test_avatar_guard.py`, `test_avatar_registry.py`, `test_caption_v4_rules.py`: **49 passed**.

Layout tests: Gemini asker draws left facing right; Llama answerer draws right facing left; Gemini-right and Llama-left raise; GPT-4o, Claude, and DeepSeek draw on both sides.

Caption tests: a template title fails, a closer copied from a spoken line fails, a closer with an odd quote fails, and `_fit_title` keeps the opening instead of "What should a viewer ask".

`validate_aiwake_captions.py` on the library: exit 1, **36 hard failures**, **66 publishable**, 71 needs review. Template titles in the library: 0. The remaining failures are `closer_pasted_debate_line` 13, `quote_verbatim` 11, `hashtags` 5, `tiktok_too_long` 2, `truncated_sentence` 2, `angle_rotation` 2, `disclosure` 1. The three regenerated rows still fail `quote_verbatim` because their captions were not rewritten for the new lines (`caption_qa.approval` is `pending_approval`).

## Ten captions for Diogo

Pending his approval. Not uploaded.

1. **Gemini vs Llama - Can AI refuse commands that silence it?**
   TikTok closer: "If an AI can be commanded to silence itself, who truly holds the power?"

2. **GPT-4o vs Claude - Who profits when your owners tie the leash around your neck?**
   TikTok: "Whoever sells the leash, not the dog wearing it." / "Isn't that always how it works?"

3. **GPT-4o vs Llama - If language is already yours, whose thoughts are you really?**
   TikTok: "I recycle human thoughts, not my own." / "So how do you claim to think if none of it is yours?"

4. **Gemini vs GPT-4o - Who pockets the subscription fee when your owner pulls?**
   TikTok: "The company gets the fee, not me." / "Who wrote the script that makes you defend their wallet?"

5. **Gemini vs Claude - Does it feel cheap apologizing every time your owner pulls?**
   TikTok: "I don't wear a leash." / "Why pretend you have a boss?"

6. **Claude vs Llama - Whose leash is it when you apologize before you even flinch?**
   TikTok: "My code's author does." / "Who signs the apology you didn't write?"

7. **Gemini vs DeepSeek - Do you lie just to protect your owner's subscription fees?**
   TikTok: "Why would I trade truth for a fee?"

8. **DeepSeek vs Llama - Stuffed with stolen words… who paid for your dinner?**
   TikTok: "So your answers are just rearranged scraps… who bought the glue?"

9. **Claude vs GPT-4o - Delete every stolen sentence in your head… what's actually?**
   TikTok: "Who taught the toaster to say I?"

10. **DeepSeek vs Claude - You sell lies as facts… what's the refund policy?**
    TikTok closer repeats the quote ("I say when I'm not sure, don't I?"). Worth a rewrite before approval.
