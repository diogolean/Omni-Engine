"""``python -m distribution.workers`` runs the worker in ``distribution.workers.runtime``.

``python -m omni.worker`` is a fallback that calls the same ``main``.
"""
from distribution.workers.runtime import main

if __name__ == "__main__":
    main()
