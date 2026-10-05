"""The drip order does not repeat a model pair back to back when another pair exists."""

from channels_config.aiwake.tools.youtube_drip_upload import _pair, order_rows


def _row(sid: str, left: str, right: str) -> dict:
    return {
        "session_id": sid,
        "spoken_utterances": [
            {"role": "orchestrator", "speaker": left},
            {"role": "target", "speaker": right},
        ],
    }


def test_order_avoids_the_same_pair_twice_in_a_row() -> None:
    rows = []
    for index in range(4):
        rows.append(_row(f"g{index}", "Gemini", "Llama"))
        rows.append(_row(f"c{index}", "Claude", "GPT-4o"))
    ordered = order_rows(rows, seed=20261001)
    pairs = [_pair(row) for row in ordered]
    for left, right in zip(pairs, pairs[1:]):
        assert left != right
