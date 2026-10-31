"""The first frame is the thumbnail: scenario and characters are already on screen, never black."""
import numpy as np

from products.animind_core import scene


def test_first_frame_is_not_black():
    lines = [{"speaker": 0, "beat": "gancho", "kind": "paraphrase", "text": "A verdade existe?", "startMs": 0, "endMs": 2000},
             {"speaker": 1, "beat": "réplica", "kind": "paraphrase", "text": "Prove.", "startMs": 2200, "endMs": 3000}]
    sc = scene.Scene(template="debate", scenario="studio", rigs=[None, None], script=lines, envelope=np.ones(400) * 0.5,
                     env_fps=100, duration_s=3.5, disclaimer="Paródia", names=["Sócrates", "Nietzsche"], roles=["x", "y"])
    first = np.asarray(sc.frame(0.0).convert("RGB"), dtype=np.float32).mean()
    settled = np.asarray(sc.frame(1.0).convert("RGB"), dtype=np.float32).mean()
    assert first > 20 and first >= 0.8 * settled  # at most a short, shallow fade
