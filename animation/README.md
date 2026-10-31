# Parametric animation

This package is the product-agnostic puppet engine. Channels call it. It does not know their names.

The working name for the hosted product is **Rigly**. The dialogue director used by current shows stays `render_dynamic_animation`. New renders use a scene spec.

## Quickstart

From the repository root, with ffmpeg on `PATH`:

```bash
python -m animation.examples.make_audio /tmp/rigly-hello.wav
python -m animation render animation/examples/rigly_hello/scene.yaml \
  --audio /tmp/rigly-hello.wav \
  --output /tmp/rigly-hello.mp4
```

That writes a 1920×1080 MP4. The example character is painted at render time, so the repository does not store PNGs or the wav.

On a warm laptop, render the preview instead. It is half resolution, capped at 12 fps, and uses a fast encode:

```bash
python -m animation render animation/examples/rigly_hello/scene.yaml \
  --audio /tmp/rigly-hello.wav \
  --output /tmp/rigly-hello-preview.mp4 \
  --preview
```

`1080x1920` and `1080x1080` are the other 1080p frames. Each side must be even and at most 1920.

## Python API

```python
from animation.api import render_scene

stats = render_scene(
    "animation/examples/rigly_hello/scene.yaml",
    "/tmp/rigly-hello.wav",
    "/tmp/rigly-hello.mp4",
    preview=False,
)
```

A channel can pass a dict and skip the file:

```python
from animation.api import render_scene

render_scene(
    {
        "version": 1,
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "seed": 3,
        "characters": [{"id": "narrator", "label": "Narrator"}],
        "lines": [{"speaker": "narrator", "start": 0.0, "text": "Today."}],
    },
    "narration.wav",
    "narration.mp4",
)
```

`render_dynamic_animation` is unchanged. Existing shows keep calling it.

## Scene spec

`version` is `1`. JSON and YAML both load. Colors are `#RRGGBB` (quote them in YAML).

| Field | Role |
|---|---|
| `width`, `height`, `fps` | Frame. `fps` is 1–60. |
| `seed` | Blink schedule. The same seed repeats. |
| `stage` | `solo` (one character), `shot` (cut to the line's speaker), `duo` (everyone, up to four). |
| `focus` | Character id used by `solo`, and by `shot` before the first line. |
| `lip_sync` | `envelope` drives the mouth from the audio's loudness. `off` holds a closed mouth. |
| `background` | `color` and an optional `image` path next to the spec. |
| `characters` | `id`, `label`, `accent`, `facing` (`left` or `right`), optional `puppet` directory. |
| `pose` | Keys at `t` seconds: `head_angle` (−40 to 40), `y_offset`, `scale`, `glow`, `brightness`. |
| `expression` | Keys at `t`: `emotion` (`neutral`, `skeptical`, `conceded`, …). |
| `camera` | Keys at `t`: `mode` (`full`, `medium`, `closeup`), `zoom`, `pan_x`, `pan_y`. A later key keeps controls it does not set. |
| `lines` | `speaker`, `start`, optional `end`, `text`, `emotion`. Empty `text` keeps the mouth closed. No `lines` speaks the focus character for the whole file. |

`label` and `accent` are for the caller. The picture uses `accent` on the generated stand-in. A `puppet` directory is a real skin (`puppet.json` plus layers). When it is omitted, the engine paints a stand-in in a temp directory and deletes it after the render.

Pose and camera numbers are interpolated. Expression and camera mode hold until the next key. With no pose keys, the character idles with a small nod and a breath.

## Determinism and preview

The same spec, audio, seed, and preview flag write the same MP4. The encode uses one thread and does not stamp the clock into the file.

`--preview` does not change the spec. It renders at half resolution (still even, at least 16) and at most 12 fps.

`--timeout SECONDS` bounds the ffmpeg calls for that render. Without it, decode is capped at 60s and the encode cap grows with the duration.

## Render path

Frames are pulled one at a time into ffmpeg. A failed encode stops without buffering the rest of the sequence. The scene renderer is an ffmpeg pipe with a timeout on every call. The package has no channel list.

## Toward the hosted product

This MVP renders a spec on one machine. The hosted product still needs a job queue, stored skins, accounts, and a way to edit the spec without a file. Phonetic mouths (Rhubarb) stay on `render_dynamic_animation` when a turn has its own audio file; the scene spec uses the loudness envelope so a render does not download a recognizer. Burned-in captions are not in the scene spec yet.
