# Sophie Bennet prompt guide

The engine builds every positive prompt. You fill scene slots. You do not write the trigger, the quality suffix, or the negative list by hand.

```
Sophie Bennet, {expression}, {pose}, {framing}, wearing {outfit}, {setting}, {lighting}, {quality suffix}
```

`Sophie Bennet,` is the whole identity. The LoRA was trained on that phrase. Extra identity words pull the face off the reference.

## Rules that the code enforces

- The prompt starts with `Sophie Bennet,`.
- Do not describe age, eye color, or hair. `blue eyes`, `blonde`, `brown hair`, `25 years old`, and `freckles` are rejected. Freckles were a tested miss.
- Frame waist-up, facing the camera. Three-quarter and full-body shots lose the likeness. Every shipped template uses `waist-up portrait facing the camera`.
- Name a top and a bottom in `outfit`. `a navy bikini top and high-waisted navy bikini bottoms` is valid. `a dress` or `a bikini` alone is not, because one of the garments is unnamed.
- Keep it non-explicit. Nude, topless, and the other words in the safety list are rejected in the positive prompt.
- Negatives are merged in code. A template cannot delete `nude`, `topless`, `nipples`, `exposed genitals`, `explicit`, `child`, `teen`, or `underage`.

The style negatives from testing (cartoon, bad hands, watermark, flat chest, wall artifacts, and the rest) live in `avatar.yaml` and are merged in front of the safety list.

## Slots

| Slot | What to write |
|---|---|
| `outfit` | A named top and a named bottom. Say the fabric when it matters (`opaque black lace bra`). |
| `setting` | A place, starting with `in` or `on` so the sentence reads cleanly. |
| `pose` | Body position. Keep the shoulders toward the camera. |
| `expression` | Mouth and mood. Do not mention eyes. |
| `lighting` | Light only. `soft morning window light`, `warm lamp light`, `shaded daylight`. |
| `framing` | Keep `waist-up portrait facing the camera`. |

## Sets

| Set | Use |
|---|---|
| `lifestyle` | Cafe, kitchen, street, bookstore. Everyday clothes. |
| `fashion` | Studio and city clothes. Both pieces named. |
| `beach_bikini` | Swimwear. The top and the bottoms are separate words. |
| `fitness` | Sports bra plus shorts or leggings. |
| `lingerie_boudoir` | Opaque bra and panties, waist-up, plain wall or window. Tasteful, not explicit. |

Seeds change the image. The words stay inside the set. A batch of a few hundred cycles these templates and stores the exact prompt, seed, preset, and file in `manifest.jsonl`.

## Adding a template

Add an item under the set in `prompts/templates.yaml`. Then run the unit tests. They render every template and reject a slot that breaks the rules above.

```yaml
- id: rainy_window
  outfit: a grey wool sweater and dark straight jeans
  setting: in a living room by a rainy window
  pose: seated with shoulders square to the camera
  expression: calm neutral expression
  lighting: soft morning window light
  framing: waist-up portrait facing the camera
```

## What testing already ruled out

- Skin-texture LoRA at 0.25 or higher changes the face. Sophie stays at 0.10 for `skin_texture_XL_v4` and `skin_realism_imperfections_xl`.
- `dpmpp_2m_sde` changes the composition. Both presets use `dpmpp_2m` / `karras`, 30 steps, cfg 5, 832×1216.
- FaceDetailer deformed the face in the fast tests. It is off on `fast` (about 20–40 seconds, one pass). `ultra_detail` adds it as an optional second pass: face `bbox/face_yolov8m.pt` at denoise 0.48, hands `bbox/hand_yolov8s.pt` at denoise 0.38, identity LoRA at 0.85 only on that detailer branch.
