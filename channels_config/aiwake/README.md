# Aiwake channel slot

The show lives in `products/aiwake/`. This directory keeps the channel store (`store/`, including the tracked `store/portfolio/` snapshots) and the channel `.gitignore`.

`import channels_config.aiwake` still works. Those modules re-export `products.aiwake` until M14.

Runtime outputs stay `{OUTPUT_PATH}/aiwake/`. The content library stays `channels_config/aiwake/store/content_library.json`. Puppets stay `{ASSETS_PATH}/puppets/`.

Horizontal episodes:

```bash
python tools/channel_cli.py --channel aiwake longform --offline --target-duration 360
```

Usage, the Shorts cut, and the cost note are in `products/aiwake/longform/README.md`.
