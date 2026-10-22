# -*- coding: utf-8 -*-
"""Print the object resolved for each spoken beat. No image calls.

    python -m modules.reel_grounding.report "Lost OOPArts" "script text"
"""
from __future__ import annotations

import sys

from production.formats.economic_reel.grounding.prompt import format_resolved_objects
from modules.reel_qa.beats import split_sentences


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    topic = args[0] if args else ""
    script = args[1] if len(args) > 1 else sys.stdin.read()
    beats = split_sentences(script)
    egypt = ["pyramid", "egyptian", "egypt", "pharaoh", "sphinx"]
    print(format_resolved_objects(topic, script, beats, banned=egypt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
