# -*- coding: utf-8 -*-
"""Print a shot list and the rendered prompts. No image generation.

    python -m production.formats.economic_reel.shot_planner.report "Lost OOPArts" "script text"

With ``REEL_SHOT_PLANNER=1`` the same command uses the planner call and,
if that JSON fails, prints the deterministic list instead. The default
prints the deterministic list and does not call a model.
"""
from __future__ import annotations

from shared.channel_flags import sole_page

import json
import sys

from production.formats.economic_reel.shot_planner.build import build_shot_list
from production.formats.economic_reel.shot_planner.entities import load_channel_config
from production.formats.economic_reel.shot_planner.plan import plan_for_reel, planner_enabled
from production.formats.economic_reel.shot_planner.render import render_shot
from production.formats.economic_reel.shot_planner.schema import ShotList


def _format(plan: ShotList, heading: str) -> str:
    config = load_channel_config(plan.channel)
    lines = [
        heading,
        json.dumps(plan.to_dict(), indent=2, ensure_ascii=False),
        "",
    ]
    for shot in plan.shots:
        rendered = render_shot(shot, config, channel=plan.channel)
        scene = shot.scene_type or shot.image_role or "object"
        lines.append(f"--- {shot.id} scene={scene} {shot.subject}")
        lines.append(rendered.positive)
        lines.append(f"NEGATIVE: {rendered.negative}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def render_report(
    topic: str, script: str, *, channel: str = sole_page("DOCUMENTARY_PIPELINE")
) -> str:
    if planner_enabled(channel):
        planned = plan_for_reel(channel=channel, topic=topic, script=script)
        if planned is not None:
            return _format(planned, "shot planner (no image call)")
        offline = build_shot_list(topic, script, channel=channel)
        return _format(
            offline,
            "shot planner invalid; deterministic list (no image call)",
        )
    plan = build_shot_list(topic, script, channel=channel)
    return _format(plan, "offline shot list (no image call, no model call)")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    topic = args[0] if args else ""
    script = args[1] if len(args) > 1 else sys.stdin.read()
    sys.stdout.write(render_report(topic, script))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
