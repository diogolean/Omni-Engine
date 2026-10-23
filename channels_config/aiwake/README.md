# Aiwake channel slot

The show lives in `products/aiwake/`. This directory keeps the channel store (`store/`, including the tracked `store/portfolio/` snapshots) and the channel `.gitignore`.

`import channels_config.aiwake` still works. Those modules re-export `products.aiwake` until M14.

Runtime outputs stay `{OUTPUT_PATH}/aiwake/`. The content library stays `channels_config/aiwake/store/content_library.json`. Puppets stay `{ASSETS_PATH}/puppets/`.
