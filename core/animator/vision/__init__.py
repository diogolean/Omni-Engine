# TODO M10: move this Aiwake-specific module to products/aiwake/.
"""Computer-vision helpers for automatic puppet rigging.

The package intentionally does not import ``head_analyzer`` at import time;
that keeps ``python -m core.animator.vision.head_analyzer`` single-loaded.
"""

__all__: list[str] = []
