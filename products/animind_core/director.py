"""Director: plan, write scene by scene, repair only a failing scene, then list shots.

English is the default language. With no script key the same steps run on a
deterministic bank so tests and dry benchmarks stay offline. Every model call
logs tokens and cost through :mod:`products.animind_core.telemetry`.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import llm_providers, settings, telemetry

CAMERAS = ("wide", "close on speaker", "reverse")
EMOTIONS = {
    "hook": "curious",
    "conflict": "tense",
    "turn": "surprised",
    "ending": "calm",
}


def direct(
    topic: str,
    names: list[str],
    template: str = "debate",
    *,
    lang: str = "en-US",
    use_llm: bool | None = None,
    cache_dir: Path | None = None,
    only_provider: str | None = None,
) -> dict[str, Any]:
    """Story plan, scene prose, one rewrite pass, and a camera/emotion shot list."""
    lang = lang or "en-US"
    template = "monologue" if template in {"monologue", "solo"} else "debate"
    names = [name.strip() or "Speaker" for name in (names or ["Ada"])]
    if template == "debate" and len(names) < 2:
        names = [names[0], "Ben"]
    topic = " ".join((topic or "why students should verify claims made by AI").split())
    if use_llm is None:
        use_llm = settings.script_llm_available() or llm_providers.OpenRouterProvider().available()
    cache_dir = cache_dir or (settings.data_dir() / "cache" / "director")
    calls: list[dict[str, Any]] = []
    if use_llm:
        plan = _llm_json(
            _plan_prompt(topic, names, template, lang), "director-plan", cache_dir, calls, only_provider,
        )
        if not isinstance(plan, dict) or not plan.get("scenes"):
            plan = plan_offline(topic, names, template, lang)
    else:
        plan = plan_offline(topic, names, template, lang)
        calls.append(_offline_row("director-plan"))
    scenes = []
    prior: list[str] = []
    for scene in plan["scenes"]:
        if use_llm:
            written = _llm_json(
                _scene_prompt(plan, scene, names, prior, lang),
                "director-scene", cache_dir, calls, only_provider,
            )
            text = str((written or {}).get("text") or "").strip()
            if not text:
                text = _scene_text(plan, scene, names, prior)
        else:
            text = _scene_text(plan, scene, names, prior)
            calls.append(_offline_row("director-scene"))
        row = {
            "id": int(scene["id"]),
            "purpose": scene.get("purpose") or "conflict",
            "speaker": _speaker(scene, names),
            "summary": scene.get("summary") or "",
            "text": text,
        }
        scenes.append(row)
        prior.append(row["summary"] or row["text"])
    scenes, rewritten = repair_scenes(
        plan, scenes, names, use_llm=use_llm, cache_dir=cache_dir, calls=calls, only_provider=only_provider,
    )
    shots = shot_list(scenes, names)
    calls.append(telemetry.record(
        "director-shots", provider="offline", model="shot-list", status="ok",
        metadata={"shots": len(shots), "cameras": sorted({shot["camera"] for shot in shots})},
    ))
    problems = [item for scene in scenes for item in scene_problems(plan, scene)]
    cost = round(sum(float(row.get("costUsd") or 0) for row in calls), 8)
    provider = next((row["provider"] for row in calls if row.get("provider") not in {None, "offline"}), "offline")
    return {
        "language": lang,
        "template": template,
        "topic": topic,
        "names": names,
        "plan": plan,
        "scenes": scenes,
        "rewrittenScenes": rewritten,
        "shots": shots,
        "scriptQA": {"pass": not problems, "problems": problems},
        "provider": provider,
        "model": next((row.get("model") for row in calls if row.get("provider") == provider), "offline"),
        "costUsd": cost,
        "inputTokens": sum(int(row.get("inputTokens") or 0) for row in calls),
        "outputTokens": sum(int(row.get("outputTokens") or 0) for row in calls),
        "calls": calls,
    }


def plan_offline(topic: str, names: list[str], template: str, lang: str = "en-US") -> dict[str, Any]:
    lead, other = names[0], names[1] if len(names) > 1 else names[0]
    if template == "debate":
        premise = f"{lead} and {other} argue about {topic}."
        conflict = f"{lead} wants a careful check. {other} wants a fast answer."
    else:
        premise = f"{lead} explains {topic} in one short monologue."
        conflict = f"{lead} has to make {topic} concrete before the close."
    scenes = [
        {"id": 1, "purpose": "hook", "speaker": 0, "summary": f"Open on why {topic} matters now."},
        {"id": 2, "purpose": "conflict", "speaker": 1 if template == "debate" else 0,
         "summary": conflict},
        {"id": 3, "purpose": "turn", "speaker": 0, "summary": f"A specific example shows why {topic} cannot be skipped."},
        {"id": 4, "purpose": "ending", "speaker": 0 if template == "monologue" else 1,
         "summary": f"Leave the audience one check they can do today about {topic}."},
    ]
    return {
        "premise": premise,
        "goals": [f"Make the audience care about {topic}.", "End on a clear decision."],
        "conflict": conflict,
        "turn": scenes[2]["summary"],
        "ending": scenes[3]["summary"],
        "scenes": scenes,
        "language": lang,
        "topic": topic,
        "template": template,
    }


def scene_problems(plan: dict[str, Any], scene: dict[str, Any]) -> list[str]:
    text = str(scene.get("text") or "").strip()
    problems = []
    if len(text.split()) < 8:
        problems.append(f"scene {scene.get('id')} is too short")
    topic = str(plan.get("topic") or "")
    stems = [word.lower().strip(".,?") for word in topic.split() if len(word) > 4]
    if stems and not any(stem in text.lower() for stem in stems):
        problems.append(f"scene {scene.get('id')} leaves the topic")
    return problems


def repair_scenes(
    plan: dict[str, Any],
    scenes: list[dict[str, Any]],
    names: list[str],
    *,
    use_llm: bool = False,
    cache_dir: Path | None = None,
    calls: list[dict[str, Any]] | None = None,
    only_provider: str | None = None,
) -> tuple[list[dict[str, Any]], list[int]]:
    """Rewrite only the scenes that fail QA. Passing scenes stay byte for byte."""
    calls = calls if calls is not None else []
    rewritten: list[int] = []
    fixed: list[dict[str, Any]] = []
    prior: list[str] = []
    for scene in scenes:
        problems = scene_problems(plan, scene)
        if not problems:
            fixed.append(dict(scene))
        else:
            replacement = dict(scene)
            if use_llm and cache_dir is not None:
                got = _llm_json(
                    _rewrite_prompt(plan, scene, names, prior, problems),
                    "director-rewrite", cache_dir, calls, only_provider,
                )
                text = str((got or {}).get("text") or "").strip()
            else:
                text = ""
                calls.append(_offline_row("director-rewrite"))
            if not text or scene_problems(plan, {**scene, "text": text}):
                text = _scene_text(plan, scene, names, prior)
            replacement["text"] = text
            fixed.append(replacement)
            rewritten.append(int(scene["id"]))
        prior.append(str(fixed[-1].get("summary") or fixed[-1]["text"]))
    return fixed, rewritten


def shot_list(scenes: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    shots: list[dict[str, Any]] = []
    for scene in scenes:
        purpose = str(scene.get("purpose") or "conflict")
        speaker = int(scene.get("speaker") or 0) % max(1, len(names))
        emotion = EMOTIONS.get(purpose, "calm")
        camera = "wide" if purpose in {"hook", "ending"} else "close on speaker"
        shots.append({
            "scene": int(scene["id"]),
            "camera": camera,
            "emotion": emotion,
            "speaker": speaker,
            "speakerName": names[speaker],
            "line": scene.get("text") or "",
        })
        if purpose == "conflict" and len(names) > 1:
            other = 1 - speaker if speaker in {0, 1} else 0
            shots.append({
                "scene": int(scene["id"]),
                "camera": "reverse",
                "emotion": "tense",
                "speaker": other,
                "speakerName": names[other],
                "line": scene.get("text") or "",
            })
    for shot in shots:
        if shot["camera"] not in CAMERAS:
            shot["camera"] = "wide"
    return shots


def _speaker(scene: dict[str, Any], names: list[str]) -> int:
    try:
        return int(scene.get("speaker") or 0) % max(1, len(names))
    except (TypeError, ValueError):
        return 0


def _scene_text(plan: dict[str, Any], scene: dict[str, Any], names: list[str], prior: list[str]) -> str:
    speaker = names[_speaker(scene, names)]
    purpose = str(scene.get("purpose") or "conflict")
    topic = str(plan.get("topic") or "the topic")
    bridge = f"After {prior[-1]} " if prior else ""
    line = {
        "hook": f"{bridge}{speaker} asks why anyone should trust a claim about {topic} without looking it up.",
        "conflict": f"{bridge}{speaker} pushes back: a fast answer about {topic} is not the same as a checked one.",
        "turn": f"{bridge}{speaker} turns the argument with one concrete case where {topic} changed the outcome.",
        "ending": f"{bridge}{speaker} closes on a single check the audience can do today about {topic}.",
    }[purpose if purpose in EMOTIONS else "conflict"]
    return " ".join(line.split())


def _plan_prompt(topic: str, names: list[str], template: str, lang: str) -> str:
    return (
        f"Write a story plan in {lang} for a {template} about {topic}. "
        f"Speakers: {', '.join(names)}. Return JSON only with keys premise, goals, conflict, turn, ending, scenes. "
        "scenes is a list of 4 objects with id, purpose (hook, conflict, turn, ending), speaker index, and summary."
    )


def _scene_prompt(plan: dict[str, Any], scene: dict[str, Any], names: list[str], prior: list[str], lang: str) -> str:
    return (
        f"Write scene {scene.get('id')} in {lang}. Purpose: {scene.get('purpose')}. "
        f"Speaker: {names[_speaker(scene, names)]}. Topic: {plan.get('topic')}. "
        f"Prior scene summaries: {' | '.join(prior) or 'none'}. "
        "One spoken sentence of at least 8 words. JSON only: {\"text\": \"...\"}."
    )


def _rewrite_prompt(plan: dict[str, Any], scene: dict[str, Any], names: list[str], prior: list[str], problems: list[str]) -> str:
    return (
        f"Rewrite only scene {scene.get('id')}. Problems: {'; '.join(problems)}. "
        f"Keep the purpose {scene.get('purpose')} and the topic {plan.get('topic')}. "
        f"Speaker: {names[_speaker(scene, names)]}. Prior: {' | '.join(prior) or 'none'}. "
        "JSON only: {\"text\": \"...\"}."
    )


def _offline_row(operation: str) -> dict[str, Any]:
    row = telemetry.record(operation, provider="offline", model="director-template", status="offline")
    return row


def _llm_json(
    prompt: str,
    operation: str,
    cache_dir: Path,
    calls: list[dict[str, Any]],
    only_provider: str | None = None,
) -> dict[str, Any] | None:
    started = time.perf_counter()

    def gemini_text(text: str) -> str:
        import os  # noqa: PLC0415

        from google.genai import types  # noqa: PLC0415

        from production.cost.google_guardrail import (  # noqa: PLC0415
            guarded_generate_content,
            make_guarded_gemini_client,
        )

        client = make_guarded_gemini_client((os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip())
        response = guarded_generate_content(
            client, model=llm_providers.GEMINI_MODEL, contents=text,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.4),
            kind="text", source=f"animind_core.{operation}",
        )
        return response.text or ""

    result, _failures = llm_providers.generate_text(prompt, cache_dir, gemini_text, only=only_provider)
    if result is None:
        row = telemetry.record(
            operation, status="failed", latency_ms=(time.perf_counter() - started) * 1000,
        )
        calls.append(row)
        return None
    row = telemetry.record(
        operation, provider=result.provider, model=result.model,
        input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cost_usd=result.cost_usd, latency_ms=(time.perf_counter() - started) * 1000,
        status="cached" if result.cached else "ok",
    )
    calls.append(row)
    try:
        return llm_providers._json_object(result.text)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
