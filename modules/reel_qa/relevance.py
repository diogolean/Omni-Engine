# -*- coding: utf-8 -*-
"""Spoken-line relevance. Fail closed when every vision backend fails."""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

from modules.reel_qa.config import gemini_model, model_chain, relevance_threshold

_LOG = logging.getLogger(__name__)

_GATE_PROMPT = """You are a strict documentary still judge. You are shown the IMAGE.
Judge the picture. The prompt is context and is not evidence that the picture is correct.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "contains_text": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the picture: place, objects, whether it is a
photograph or CGI or a miniature or an illustration, and any anatomy you can see.
Set anachronism true for a modern, sci-fi, steampunk, or futuristic object in an ancient
or museum documentary beat, unless the spoken beat itself asks for that object.
Set non_photoreal true for CGI, a 3D render, an illustration, a cartoon, or a miniature
or clay-model look.
Set anatomy_defect true for a mangled, fused, or extra limb.
Set contains_text true when the picture has burned-in words, a date, or a caption that
covers the frame.
A real photograph of the artefact in a museum, a display case, or on a plain background
is the correct subject. A small label, a scale bar, or the glass case does not make it
a text panel and does not make it an illustration. Set contains_text false for that
label or scale bar. Set non_photoreal false for that photograph. Set anachronism false
for the case, the scale bar, or the catalogue label. Do not set setting_contradicts_beat
because the photograph is indoors, in a museum, or not outdoors in the ancient landscape.
Set those flags only for a true illustration, diagram, map, drawing, CGI, or a slide
or text panel that does not show the object.
Set setting_contradicts_beat true only when the picture shows a different monument
or a different region.
A visible expected subject is not enough. The wrong place, a non-photoreal look, or a
broken body still fails.
is_relevant is true only when every flag above is false and the picture shows the subject.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
Spoken beat: {chunk_text}
Image prompt: {prompt}
The image prompt's EXPECTED SUBJECT or THIS BEAT'S OBJECT is the object this still must show.
When the spoken beat is abstract and names no object, the picture must still show that
subject, as a photoreal documentary photograph, in a setting the beat allows.
"""

_SHOT_PROMPT = """You are a strict documentary still judge. You are shown the IMAGE.
This still was planned as a real place or scene. Judge whether it is a real,
plausible, photoreal place or scene for this topic and era, and whether it
matches the PLANNED SHOT. The spoken line is context only. Do not fail the
picture because the spoken line names an artefact, fragments, gears, or a
pendant that the planned shot deliberately leaves out.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "contains_forbidden_object": boolean,
  "contains_text": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the picture: place, objects, whether it is a
photograph or CGI or a miniature or an illustration, and any anatomy you can see.
Set anachronism true for a modern, sci-fi, steampunk, or futuristic object, unless
the planned shot itself asks for that object.
Set non_photoreal true for CGI, a 3D render, an illustration, a cartoon, or a miniature
or clay-model look.
Set anatomy_defect true for a mangled, fused, or extra limb.
Set contains_text true when the picture has burned-in words, a museum label, a date, or a caption.
Set setting_contradicts_beat true for sand dunes, mesas, or the Sahara when the geography is not a sand desert.
Set setting_contradicts_beat true only when the place is a different region than the
planned setting. A desert mesa or Egyptian sand ruins fails when the plan names the
Colombian Andes. Do not set it because the spoken line mentions an object that is
absent. Do not compare the place to the spoken line.
Set contains_forbidden_object true only when the picture itself clearly shows a named
artefact, gears, a pendant, a gold ornament, or a figurine. Do not set it because
the spoken line or the prompt mentions those words.
Set shows_expected_subject true when the planned place or scene is visible.
is_relevant is true only when every flag above is false and the planned scene is visible.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
Do not score artefact shape. shape_mismatch does not apply to this still.
Topic: {topic}
Geography: {geography}
PLANNED SHOT
Scene: {scene_type}
Subject: {shot_subject}
Setting: {shot_setting}
Era: {shot_era}
Spoken line, context only, not the subject: {chunk_text}
Image prompt: {prompt}
"""

_EVENT_PROMPT = """You are a strict documentary still judge. You are shown an ARCHIVAL PHOTOGRAPH
of a historical event. Score it as a record of that event: the people, the boat,
the coast, or the year. The artefact itself may be absent. Do not score artefact
shape. Do not ask for gears, fragments, or a mechanism.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "contains_text": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the photograph.
Set contains_text true when the picture has burned-in words, a museum label, a date, or a caption.
Set anachronism true for modern scuba, a wetsuit, or a modern boat when the event
is the 1900-1901 wreck.
Set non_photoreal true for CGI, a render, or an illustration.
Set setting_contradicts_beat true only when the photograph is a different event
(a museum gallery of the object, with no people and no boat).
A real photograph of divers, a period boat, or the coast passes even when the
artefact is not in the frame.
is_relevant is true only when every flag above is false.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
Do not score artefact shape. An event photograph is not an object still.
Event: {chunk_text}
Era: {shot_era}
Setting: {shot_setting}
"""

_SITE_PROMPT = """You are a strict documentary still judge. You are shown a REAL PHOTOGRAPH
of an archaeological site. Score the site itself: the stones, the enclosure,
the pillars, or the ground. Modern conservation at a dig is expected.
Return STRICT JSON only — no markdown, no commentary:
{{
  "image_description": string,
  "shows_expected_subject": boolean,
  "anachronism": boolean,
  "non_photoreal": boolean,
  "anatomy_defect": boolean,
  "setting_contradicts_beat": boolean,
  "contains_text": boolean,
  "is_relevant": boolean,
  "relevance_score": float,
  "detected_subjects": [string],
  "rejection_reason": string
}}
image_description is a literal caption of the photograph.
A protective roof, a walkway, a shelter, a fence, a canopy, or scaffolding
over the ancient stones is conservation. Do not set anachronism for those.
Set contains_text true when the picture has burned-in words, a museum label, a date, or a caption.
Set anachronism true for scuba, a wetsuit, a smartphone, a car, or an airplane.
Set setting_contradicts_beat true for sand dunes, mesas, or the Sahara when the geography is not a sand desert.
Set non_photoreal true for CGI, a render, or an illustration.
Set setting_contradicts_beat true when the photograph is a different place,
or when it shows a lake, river, or sea and the geography is arid.
is_relevant is true only when every flag above is false.
relevance_score is 0.0–1.0. rejection_reason is null only when is_relevant is true
and relevance_score >= {threshold:.2f}.
Do not score artefact shape. A site photograph is not an object still.
Site: {chunk_text}
Topic: {topic}
Geography: {geography}
Era: {shot_era}
Setting: {shot_setting}
"""

_HARD_MODERN = re.compile(
    r"\b(?:scuba|wetsuit|smartphone|cell phone|mobile phone|automobile|"
    r"airplane|aeroplane|helicopter|car)\b"
)
_CONSERVATION = re.compile(
    r"\b(?:roofs?|walkways?|shelters?|fences?|canop(?:y|ies)|boardwalks?|"
    r"scaffold(?:ing)?|conservation|railings?)\b"
)
_ARID_GEO = re.compile(
    r"arid|dry limestone|no lake|desert plateau|no river"
)
_WATER = re.compile(
    r"\b(?:lakes?|rivers?|seas?|oceans?|waterfalls?|oasis|ponds?|shorelines?)\b"
)


_DUNE = re.compile(r"\b(?:sand dunes?|dunes|mesas?|sahara|sand sea)\b")
_SAND_DESERT_GEO = re.compile(r"dune|sahara|erg|sand sea|\bdesert\b")


def dune_conflict(description: str, geography: str) -> bool:
    """True when a non-desert region is shown as dunes, mesas, or the Sahara."""
    place = (geography or "").lower()
    if not place.strip() or _SAND_DESERT_GEO.search(place):
        return False
    text = (description or "").lower()
    for match in _DUNE.finditer(text):
        prefix = text[max(0, match.start() - 16):match.start()]
        if re.search(r"\b(?:no|not|without|never)\s*$", prefix):
            continue
        return True
    return False


def arid_water_conflict(description: str, geography: str) -> bool:
    """True when an arid region template is illustrated with open water."""
    if not _ARID_GEO.search((geography or "").lower()):
        return False
    text = (description or "").lower()
    for match in _WATER.finditer(text):
        prefix = text[max(0, match.start() - 16):match.start()]
        if re.search(r"\b(?:no|not|without|never)\s*$", prefix):
            continue
        return True
    return False


def _conservation_only(description: str) -> bool:
    """A modern roof or walkway at a dig, with no hard modern object."""
    text = (description or "").lower()
    if _HARD_MODERN.search(text):
        return False
    return _CONSERVATION.search(text) is not None

# A Flux scene must not contain these. An object photograph may.
_FORBIDDEN_CAPTION = (
    "gear wheel",
    "gears",
    "gear",
    "pendant",
    "gold ornament",
    "golden ornament",
    "gold bird",
    "figurine",
    "corroded fragment",
    "bronze fragment",
)


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        "is_relevant": False,
        "relevance_score": 0.0,
        "detected_subjects": [],
        "rejection_reason": reason,
        "qa_status": "unavailable",
        "publishable": False,
        "hard_reject": False,
        "banned_hits": [],
        "provider": None,
        "model_id": None,
    }


def _provider(name: str):
    from agents.media.providers.visual_eval import resolve_visual_eval_provider

    key = (name or "").strip().lower()
    if key == "gemini":
        return resolve_visual_eval_provider(provider="gemini", model_id=gemini_model())
    if key in {"qwen", "openrouter", "deepseek"}:
        return resolve_visual_eval_provider(provider="qwen")
    return resolve_visual_eval_provider(provider=key)


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes"}
    return value is True


def _caption_forbidden(description: str) -> bool:
    """True when a Flux still's caption names an object the scene must not show."""
    caption = " ".join((description or "").lower().split())
    if not caption:
        return False
    for phrase in _FORBIDDEN_CAPTION:
        if phrase in caption:
            prefix = caption[max(0, caption.find(phrase) - 16):caption.find(phrase)]
            if prefix.rstrip().endswith(("no", "not", "without", "never")):
                continue
            return True
    return False


def _verdict_from_payload(
    data: dict[str, Any],
    *,
    image_path: Path,
    chunk_text: str,
    banned_subjects: list[str] | None,
    provider: str,
    model_id: str,
    score_against: str = "narration",
    geography: str = "",
) -> dict[str, Any]:
    from modules.reel_qa.documentary import documentary_failures
    from modules.reel_visual_qa import apply_banned_subject_gate

    threshold = relevance_threshold()
    score = float(data.get("relevance_score") or 0.0)
    score = max(0.0, min(1.0, score))
    subjects = data.get("detected_subjects") or []
    if not isinstance(subjects, list):
        subjects = [str(subjects)]
    subjects = [str(item) for item in subjects][:12]
    described = " ".join(
        part for part in (
            str(data.get("image_description") or ""),
            " ".join(subjects),
        ) if part
    )
    defects = documentary_failures(described, spoken=chunk_text)
    for key, label in (
        ("anachronism", "anachronism"),
        ("non_photoreal", "non_photoreal"),
        ("anatomy_defect", "anatomy"),
        ("setting_contradicts_beat", "setting_contradicts_beat"),
        ("contains_text", "contains_text"),
    ):
        if _as_bool(data.get(key)) and label not in defects:
            defects.append(label)
    if (
        score_against in {"event", "site"}
        and "anachronism" in defects
        and _conservation_only(described)
    ):
        defects = [item for item in defects if item != "anachronism"]
    if arid_water_conflict(described, geography) and score_against != "event":
        if "setting_contradicts_beat" not in defects:
            defects.append("setting_contradicts_beat")
    if dune_conflict(described, geography) and score_against != "event":
        if "sand_dunes" not in defects:
            defects.append("sand_dunes")
    if score_against == "shot" and (
        _as_bool(data.get("contains_forbidden_object")) or _caption_forbidden(described)
    ):
        if "contains_forbidden_object" not in defects:
            defects.append("contains_forbidden_object")
    from modules.reel_artefact.asset_pool import museum_object_photo

    museum = score_against == "narration" and museum_object_photo(described)
    if museum:
        drop = {
            "contains_text",
            "non_photoreal",
            "setting_contradicts_beat",
            "anachronism",
        }
        defects = [item for item in defects if item not in drop]
    stated = bool(data.get("is_relevant"))
    if museum and not defects:
        score = max(score, threshold)
        stated = True
    relevant = stated and score >= threshold and not defects
    reason = data.get("rejection_reason")
    if defects:
        relevant = False
        score = min(score, 0.35)
        reason = "documentary_fail:" + ",".join(defects)
    elif score >= threshold and relevant:
        reason = None
    elif not reason:
        reason = "below_relevance_threshold"
    verdict = apply_banned_subject_gate(
        {
            "is_relevant": relevant,
            "relevance_score": score,
            "detected_subjects": [str(s) for s in subjects][:12],
            "rejection_reason": reason,
        },
        spoken_text=chunk_text,
        banned_subjects=banned_subjects,
    )
    # shape_mismatch is only for an object photograph. A scene, an event,
    # and an archival boat are not failed because the artefact is absent.
    if score_against == "narration" and _as_bool(data.get("shape_mismatch")) and "shape_mismatch" not in defects:
        defects.append("shape_mismatch")
        relevant = False
        score = min(score, 0.35)
        reason = "documentary_fail:" + ",".join(defects)
        verdict["is_relevant"] = False
        verdict["relevance_score"] = score
        verdict["rejection_reason"] = reason
    if defects:
        verdict["is_relevant"] = False
        verdict["hard_reject"] = True
        verdict["publishable"] = False
        verdict["documentary_defects"] = list(defects)
        verdict["rejection_reason"] = "documentary_fail:" + ",".join(defects)
    verdict["qa_status"] = "scored"
    verdict["publishable"] = bool(verdict.get("is_relevant")) and not verdict.get("hard_reject")
    verdict["image_description"] = str(data.get("image_description") or "")[:500]
    text_flag = _as_bool(data.get("contains_text")) or "contains_text" in defects
    drawn_flag = _as_bool(data.get("non_photoreal")) or "non_photoreal" in defects
    if museum and "contains_text" not in defects:
        text_flag = False
    if museum and "non_photoreal" not in defects:
        drawn_flag = False
    verdict["contains_text"] = text_flag
    verdict["non_photoreal"] = drawn_flag
    verdict["provider"] = provider
    verdict["model_id"] = model_id
    _LOG.info(
        "VisualQA | file=%s provider=%s model=%s relevant=%s score=%.2f reason=%s",
        image_path.name,
        provider,
        model_id,
        verdict.get("is_relevant"),
        score,
        verdict.get("rejection_reason"),
    )
    return verdict


def _local_screen(path: Path, *, narration: str, against: str) -> dict[str, Any] | None:
    """Hard local failures return a verdict. Borderline and clean stills return None.

    A clean still still reaches the vision judge: a sharp, large, unique photo
    can be the wrong subject. The local layer only removes the cases that do
    not need a model at all.
    """
    mode = (os.getenv("REEL_LOCAL_GATE") or "1").strip().lower()
    if mode in {"0", "off", "false", "no"}:
        return None
    try:
        from modules.reel_qa.local_gate import check_still
    except Exception:  # noqa: BLE001
        return None
    result = check_still(path, narration=narration, vertical=True, remember=False)
    if result.ok or result.needs_llm:
        return None
    verdict = _unavailable("local:" + ",".join(result.reasons))
    verdict["qa_status"] = "scored"
    verdict["local_gate"] = result.as_dict()
    verdict["hard_reject"] = True
    _LOG.info(
        "VisualQA local reject | file=%s | %s", path.name, ",".join(result.reasons),
    )
    return verdict


def call_judge(judge: Callable[..., dict[str, Any]], image, text: str, prompt: str, **extra):
    """Call a judge with the kwargs it accepts.

    Stills and final frames share this so a test double that only takes
    ``banned_subjects`` still runs, and the real judge can receive the
    reference photograph.
    """
    import inspect

    kwargs = {key: value for key, value in extra.items() if value is not None}
    try:
        signature = inspect.signature(judge)
    except (TypeError, ValueError):
        signature = None
    if signature is not None:
        if not any(
            item.kind == inspect.Parameter.VAR_KEYWORD
            for item in signature.parameters.values()
        ):
            kwargs = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return judge(image, text, prompt, **kwargs)


def evaluate_chunk_relevance(
    image_path: Path | str,
    current_chunk_text: str,
    image_generation_prompt: str = "",
    banned_subjects: list[str] | None = None,
    *,
    providers: list[str] | None = None,
    reference_image: str | Path | None = None,
    canonical_shape: str = "",
    score_against: str = "narration",
    shot_subject: str = "",
    shot_setting: str = "",
    shot_era: str = "",
    topic: str = "",
    scene_type: str = "",
    geography: str = "",
) -> dict[str, Any]:
    """Score one still.

    ``narration`` is an object beat and may include artefact shape.
    ``shot`` is a planned Flux scene. ``event`` is an archival photograph.
    Tries ``REEL_QA_MODEL_CHAIN``. A missing key, a down provider, or
    unparseable output is not a pass.
    """
    from modules.reel_visual_qa import _strip_json

    path = Path(image_path)
    if not path.is_file():
        return _unavailable("missing_image")

    local = _local_screen(
        path,
        narration=current_chunk_text,
        against=(score_against or "narration").strip().lower(),
    )
    if local is not None:
        return local

    chain = list(providers or model_chain())
    threshold = relevance_threshold()
    against = (score_against or "narration").strip().lower()
    if against == "shot":
        prompt = _SHOT_PROMPT.format(
            threshold=threshold,
            topic=(topic or "this episode").strip()[:180],
            geography=(geography or shot_setting or "the planned place").strip()[:240],
            scene_type=(scene_type or "the planned scene").strip()[:80],
            shot_subject=(shot_subject or "the planned subject").strip()[:240],
            shot_setting=(shot_setting or "the planned setting").strip()[:240],
            shot_era=(shot_era or "the planned era").strip()[:120],
            chunk_text=(current_chunk_text or "").strip()[:400],
            prompt=(image_generation_prompt or "").strip()[:800],
        )
    elif against == "event":
        prompt = _EVENT_PROMPT.format(
            threshold=threshold,
            chunk_text=(current_chunk_text or "").strip()[:400],
            shot_setting=(shot_setting or "the event").strip()[:240],
            shot_era=(shot_era or "the event period").strip()[:120],
        )
    elif against == "site":
        prompt = _SITE_PROMPT.format(
            threshold=threshold,
            chunk_text=(current_chunk_text or "").strip()[:400],
            topic=(topic or "this episode").strip()[:180],
            geography=(geography or shot_setting or "the site").strip()[:240],
            shot_setting=(shot_setting or "the site").strip()[:240],
            shot_era=(shot_era or "the site period").strip()[:120],
        )
    else:
        prompt = _GATE_PROMPT.format(
            threshold=threshold,
            chunk_text=(current_chunk_text or "").strip()[:400],
            prompt=(image_generation_prompt or "").strip()[:400],
        )
    images: list[Path] = [path]
    labels = ["candidate"]
    reference = Path(reference_image) if reference_image else None
    if against == "narration" and reference is not None and reference.is_file():
        shape = (canonical_shape or "the object in the reference photograph").strip()
        prompt += (
            "\nA second image labeled REFERENCE is a photograph of the real artefact. "
            f"Canonical shape: {shape}. "
            'Add "shape_mismatch": boolean. Set it true when the candidate does not '
            "show that shape. relevance_score is shape fidelity against the reference.\n"
        )
        images.append(reference)
        labels.append("reference")
    from modules.reel_artefact.provider_health import (
        is_billing_or_auth,
        mark_dead,
        provider_dead,
    )

    errors: list[str] = []
    for name in chain:
        provider_key = "openrouter" if name in {"qwen", "openrouter", "deepseek"} else name
        if provider_dead(provider_key):
            errors.append(f"{name}_dead")
            _LOG.info("VisualQA provider %s dead — trying next", name)
            continue
        provider = _provider(name)
        if provider is None:
            errors.append(f"{name}_unavailable")
            _LOG.warning("VisualQA provider %s unavailable — trying next", name)
            continue
        try:
            result = provider.evaluate(prompt, images, labels=labels)
            data = _strip_json(getattr(result, "text", "") or "")
        except Exception as exc:  # noqa: BLE001
            if is_billing_or_auth(exc):
                mark_dead(provider_key, str(exc))
            errors.append(f"{name}:{type(exc).__name__}")
            _LOG.warning("VisualQA %s failed on %s (%s)", name, path.name, exc)
            continue
        model_id = str(getattr(result, "model_id", "") or "")
        provider_name = str(getattr(result, "provider", "") or name)
        return _verdict_from_payload(
            data,
            image_path=path,
            chunk_text=current_chunk_text,
            banned_subjects=banned_subjects,
            provider=provider_name,
            model_id=model_id,
            score_against=against,
            geography=geography,
        )

    reason = "qa_unavailable:" + ",".join(errors or ["no_provider"])
    _LOG.error("VisualQA fail-closed | file=%s | %s", path.name, reason)
    return _unavailable(reason)


def evaluate_with_technical_retry(
    judge: Callable[..., dict[str, Any]],
    image: Path | str,
    text: str,
    prompt: str,
    banned_subjects: list[str] | None = None,
    *,
    reference_image: str | Path | None = None,
    canonical_shape: str = "",
    score_against: str = "narration",
    shot_subject: str = "",
    shot_setting: str = "",
    shot_era: str = "",
    topic: str = "",
    scene_type: str = "",
    geography: str = "",
) -> dict[str, Any]:
    """Score one still. Retry the judge on timeout or an invalid reply.

    A scored rejection is returned immediately. Only ``qa_status=unavailable``
    spends the extra judge calls. The image is not regenerated here.
    """
    from modules.reel_qa.config import judge_retries

    extra = judge_retries()
    verdict: dict[str, Any] = {}
    for i in range(extra + 1):
        try:
            verdict = dict(
                call_judge(
                    judge,
                    image,
                    text,
                    prompt,
                    banned_subjects=banned_subjects,
                    reference_image=reference_image or "",
                    canonical_shape=canonical_shape,
                    score_against=score_against,
                    shot_subject=shot_subject,
                    shot_setting=shot_setting,
                    shot_era=shot_era,
                    topic=topic,
                    scene_type=scene_type,
                    geography=geography,
                )
                or {}
            )
        except Exception as exc:  # noqa: BLE001
            verdict = {
                "is_relevant": False,
                "relevance_score": 0.0,
                "rejection_reason": f"qa_unavailable:{type(exc).__name__}",
                "qa_status": "unavailable",
                "publishable": False,
            }
        if verdict.get("qa_status") != "unavailable":
            return verdict
        if i < extra:
            _LOG.warning(
                "VisualQA technical failure (%s) — judge retry %d/%d",
                verdict.get("rejection_reason"),
                i + 1,
                extra,
            )
    return verdict


def verdict_passed(verdict: dict[str, Any] | None) -> bool:
    row = verdict or {}
    if row.get("qa_status") == "unavailable" or row.get("hard_reject"):
        return False
    try:
        score = float(row.get("relevance_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return bool(row.get("is_relevant")) and score >= relevance_threshold()
