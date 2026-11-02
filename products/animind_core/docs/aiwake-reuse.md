# What Animind reuses from Aiwake

Animind does not redraw the puppet. `create_avatar(description, style)` in
`products/animind_core/avatar.py` asks an image model for one bust, then
attaches the face banks Aiwake already ships. The right view is a mirror of
the left view. A second call, only when the run cap allows it, draws the
front. The same bust is cached under `ANIMIND_DATA_DIR/avatar_cache/` and
reused for every later scene.

Stick figures use that same path. The image model draws the stick figure.
`products/animind_core/stick.py` remains only for a machine with no provider
key (`ANIMIND_FORCE_MOCK=1` or an empty key).

## Factory

| Aiwake | What it does | How Animind uses it |
| --- | --- | --- |
| `products/aiwake/animator/factory/create_puppet.py` | `generate_character_image` draws on Gemini Flash image. `create_puppet` mattes and calls `auto_rig_character`. Prompts demand a 25-degree bust, a torso that exits the bottom edge, and a mouthless chin plate. | Gemini image calls go through `generate_character_image`. The bust prompt is `rig.STYLE_PROMPT` / `rig.FRONT_PROMPT`, which keep that framing. Together and OpenRouter receive the same sentence, shortened to FLUX's length limit. |
| `products/aiwake/animator/factory/one_shot_factory.py` | `OneShotPuppetFactory` generates one 9:16 bust, slices head from body on the jaw, and can generate extra facings. `fit_three_quarter_body` and `fit_to_golden_bounds` seat the figure. | Not called directly: it always spends Gemini and writes into the Aiwake asset root. Animind keeps the owner's rule that facing-right is a mirror of left, so a second generated profile is not needed. Seating is the same bottom-aligned crop. |
| `products/aiwake/animator/factory/generate_concepts.py` | Contact sheets of approval busts. `_fit_panel` scales the chest so it is cut by the bottom edge. | The idea of that fit is what `avatar._seat` does. Concept generation itself stays an Aiwake approval tool. |

## Face

| Aiwake | What it does | How Animind uses it |
| --- | --- | --- |
| `products/aiwake/animator/asset_generator.py` `_draw_ghibli_mouth_patch` | Organic Rhubarb mouths, including the asymmetric `smug_smile` and the `stressed_grimace`. | `products/animind_core/mouthset.py` calls it for visemes `X` and `A`–`H` and for the seven emotion mouths. That is the South Park construction-paper mouth, not a new drawing. |
| `products/aiwake/animator/render/skia_mouths.py` | Per-view Skia suites for the cyborg archetypes (9 visemes and 5 acting mouths). | Left in place for those puppets. A generic stick or human bust has no cyborg archetype, so Animind does not invent one. |
| `products/aiwake/animator/render/visemes.py` | Mecha palette mouths and `draw_mouths`. | Same boundary: cyborg palettes stay in Aiwake. |
| `products/aiwake/animator/render/facial_rig.py` | Brow stamps, emotion poses, mouth docking on the chin. | Emotion brow poses already used by the renderer (`scene.EMO_BROW_POSE`) are written out as one brow sprite per emotion. |
| `products/aiwake/animator/vision/head_analyzer.py` | Eye landmarks, blink-lid overlays, per-version brow seats. | Blink layers are `eyes_half` and `eyes_closed` on the detected eye line. The full per-version calibrator (`v3` … `v14`, including `v3_southpark_hybrid`) stays on the Aiwake puppets it was tuned for. |
| `animation/parametric/factory/landmark_detector.py` | MediaPipe, then the OpenCV optic/jaw fallback. | `create_avatar` places the mouth, the neck cut, and the eyes with `detect_landmarks`. |
| `animation/parametric/animator_types.py` | The nine Rhubarb visemes. | The sheet always carries `X A B C D E F G H`. |

## Debate and picture providers

Aiwake's debate playback is `products/aiwake/animator/pipeline.py` (`debate_lines`, `render_test_dialogue`) driven by `products/aiwake/orchestrator.py`. Animind's director (`products/animind_core/director.py`) is the writing side of that shape: premise, goals, conflict, turn, ending, then one scene at a time, then a shot list (`wide`, `close on speaker`, `reverse`). It does not copy the cyborg exchange renderer.

Image money goes through `products/animind_core/image_models.py`:

- Gemini Flash image, via the Aiwake function above (`GEMINI_API_KEY`)
- Together FLUX.1-schnell (`TOGETHER_API_KEY`, `TOGETHER_IMAGE_MODEL`)
- OpenRouter Qwen-Image and FLUX Kontext (`OPENROUTER_API_KEY`, `OPENROUTER_QWEN_IMAGE_MODEL`, `OPENROUTER_FLUX_KONTEXT_MODEL`)

`ANIMIND_RUN_BUDGET_USD` defaults to `0.10` for one avatar or one benchmark run. Keys are read from the environment only.
