# Eye control contract

The web already checks this shape in `docs/EYE_RIG.md` and `lib/eye-rig.ts`
on `feat/mvp-ui`. The core uses the same names so that app can read a rig
without a second adapter.

Per supplied view, the layers stay `eye_white` (sclera; a baked pupil is not
rewritten), `iris`, and `eye_socket`. Opaque socket pixels with alpha above 32
are the occlusion. `eyeRig.maxShift` and `eyeRig.irises` stay in canvas pixels,
`+x` viewer-right and `+y` down. A view that was not drawn is omitted.

The proof travel starts at `[8, 4]`, which is what `reportSeparable` runs, and
shrinks until 75% of the iris alpha stays inside the socket in both directions.
The published `maxShift` is left for the existing renderer. The markdown also
mentions starting from that published shift; the app code does not, so the core
follows the code.

`eyeControl.views[name]` is `{gaze, travel?, sockets?, reason?}`. `gaze` is
`separable` only after the pupil centroid moves left, right, up, and down and
the pixels outside the socket stay put. `eyeControl.ready` is that result for
every supplied face view. The offline stick presets pass. The procedural
Einstein bust has the three layers, but the dark pixels inside the socket do
not travel, so it stays `unavailable` and is not rewritten. `recovered` is
reserved for a flattened plate; this core does not repaint one. The web still
recovers those plates on import.

`POST /v1/characters/{id}/eyes` writes only `eye_control.json`.
`create_puppet.py` and `one_shot_factory.py` are unchanged.
