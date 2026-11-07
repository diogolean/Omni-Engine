# Eye control contract

`eyeControl` is additive on a character response and on `POST /v1/characters/{id}/eyes`.
Older clients ignore it. A missing field means the same as `ready: false`.

```json
{
  "schema": "animind.eyeControl.v1",
  "version": 1,
  "ready": false,
  "views": {
    "front": {"status": "unavailable", "reason": "layers-missing", "probes": {}},
    "left": {"status": "unavailable", "reason": "view-missing", "probes": {}},
    "right": {"status": "ready", "reason": null, "probes": {
      "center": {"pass": true, "delta": 0},
      "left": {"pass": true, "delta": -6},
      "right": {"pass": true, "delta": 6},
      "up": {"pass": true, "delta": -4},
      "down": {"pass": true, "delta": 4}
    }}
  }
}
```

`ready` is true only when front, left, and right each moved the iris centroid
center to left, right, up, and down, and the pixels outside the eye opening
stayed identical. The character field `eyeControlReady` mirrors that boolean.
Do not treat a rig as gaze-ready from `eyeRig` alone.

`reason` is `layers-missing`, `view-missing`, `no-travel`, `repeated-image`,
`identity-lost`, `off-axis`, `probe-error`, or `upgrade-aborted`.

`POST /v1/characters/{id}/eyes` writes only `eye_control.json` next to the
saved character. View PNGs, visemes, and expressions are not rewritten. If the
saved layers cannot pass the probe, the response is `ready: false` and the
original files stay as they were. No image provider is called.

The web viewer can read `eyeControl.views[name].status` before applying gaze.
`products/aiwake/animator/factory/create_puppet.py` and `one_shot_factory.py`
are unchanged; the proof runs on the layered view the core already saved.
