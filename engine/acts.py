# -*- coding: utf-8 -*-
"""acts steps moved out of main.py. Bodies are unchanged."""
from __future__ import annotations

from engine.geo_anchors import _GEO_ANCHORS
from engine.runtime import (
    Any,
    Path,
    _LOGO_ONLY_STILL_POST_TYPES,
    _MEI_DNA_FALLBACK,
    _SEQUENCE_VIDEO_POST_TYPES,
    _mei_build_act_prompts,
    _re,
    _segment_script_into_act_snippets,
    logging,
)
from engine.log import _LOG
from engine.bootstrap import REPO_ROOT
def _is_sequence_video_post(post_type: str) -> bool:
    return str(post_type or "").strip().upper() in _SEQUENCE_VIDEO_POST_TYPES
def _planner_media_url(
    post_type: str,
    *,
    imgbb_url: str = "",
    local_image: str = "",
    b2_video_url: str = "",
    video_path: str = "",
) -> str:
    """MEDIA URL for PostPlanner: stills never inherit leftover reel paths."""
    if _is_sequence_video_post(post_type) or str(post_type or "").upper().endswith("REEL"):
        return (b2_video_url or video_path or imgbb_url or local_image or "").strip()
    return (imgbb_url or local_image or "").strip()
def _is_logo_only_still(post_type: str) -> bool:
    return str(post_type or "").strip().upper() in _LOGO_ONLY_STILL_POST_TYPES
def _load_engagement_bait_examples(page_ctx: "Any | None" = None) -> str:
    """
    Read '@REDES REF SOURCE FONTE.xlsx' and return up to 30 viral bait hooks
    sampled from the three highest-signal sheets as a formatted reference block.

    Returns an empty string silently if pandas is missing or the file is absent.
    """
    try:
        import pandas as _pd  # noqa: PLC0415
        import random as _rnd_xl  # noqa: PLC0415
    except ImportError:
        return ""

    _XLSX_REL = (
        "channels_config/wonder_feed/Quotes_reference/@REDES REF SOURCE FONTE.xlsx"
    )
    try:
        _xl_abs = REPO_ROOT / _XLSX_REL
        if not _xl_abs.exists():
            _LOG.warning("Engagement bait spreadsheet not found: %s", _xl_abs)
            return ""

        _xl = _pd.ExcelFile(str(_xl_abs))
        _SHEETS = ["source quotes", "Quotes 26", "Randomize Quote"]
        _rows: list[str] = []
        for _sname in _SHEETS:
            if _sname not in _xl.sheet_names:
                continue
            _df = _pd.read_excel(_xl, sheet_name=_sname)
            _col = _df.iloc[:, 0].dropna().astype(str)
            # Keep only text rows (skip URLs, single-word cells)
            for _v in _col:
                _v = _v.strip()
                if (
                    len(_v) > 20
                    and not _v.startswith("http")
                    and "\n" not in _v[:10]
                ):
                    _rows.append(_v)

        if not _rows:
            return ""

        # Sample up to 30 unique examples, shuffled so every run gets variety
        _sample = _rnd_xl.sample(_rows, min(30, len(_rows)))
        _block = "\n".join(f"  - {r}" for r in _sample)
        _LOG.info(
            "Engagement bait loaded: %d examples from %s",
            len(_sample),
            _XLSX_REL,
        )
        return _block
    except Exception as _exc:  # noqa: BLE001
        _LOG.warning("_load_engagement_bait_examples failed (%s) — skipping.", _exc)
        return ""
def _silence_noisy_http_loggers() -> None:
    """Quiet httpx / anthropic / google SDK chatter without muting ``__main__`` run logs."""
    for name in (
        "anthropic",
        "httpx",
        "google",
        "httpcore",
        "google.genai",
        "google_genai",
        "google.auth",
        "google.cloud",
        "google.api_core",
        "urllib3",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)
def _looks_like_upstream_api_failure(exc: BaseException) -> bool:
    """Heuristic: Anthropic / Gemini transport & API errors."""
    mod = getattr(type(exc), "__module__", "") or ""
    if mod.startswith("anthropic"):
        return True
    if "anthropic." in mod:
        return True
    if "google.genai" in mod:
        return True
    return mod.startswith("httpx.") or exc.__class__.__name__.endswith("HTTPError")
def _extract_topic_visual_entities(subject: str) -> str:
    """
    Parse the topic/subject string and return a camera-perspective directive
    string grounding image generation in the specific geographic/visual entity
    named.  Falls back to extracted proper nouns when no known anchor matches.
    """
    lower = subject.lower()
    for keywords, directive_template in _GEO_ANCHORS:
        if any(kw in lower for kw in keywords):
            return directive_template.replace("{subject}", subject)
    # Generic fallback: extract capitalised phrases as visual subjects
    candidates = _re.findall(
        r'\b([A-Z][A-Za-z\'-]+(?:\s+[A-Z][A-Za-z\'-]+){0,3})\b', subject
    )
    _stopwords = {
        "The", "A", "An", "In", "At", "On", "But", "And", "Or",
        "Some", "Ancient", "This", "That", "These", "Those",
        "What", "How", "Why", "When", "Where", "Who",
        "Today", "Here", "Now", "Then", "Its", "Our", "Their",
    }
    seen: set[str] = set()
    result: list[str] = []
    for c in candidates:
        cl = c.lower()
        if c not in _stopwords and cl not in seen and len(c) > 3:
            seen.add(cl)
            result.append(c)
    return ", ".join(result[:4]) if result else ""
def _strip_audio_behavior_tags(text: str) -> str:
    """Remove bracketed emotional tags / SSML so ElevenLabs never reads them aloud."""
    if not text:
        return ""
    clean = _re.sub(r"<\s*break\s+[^>]*/?\s*>", " ... ", text, flags=_re.IGNORECASE)
    clean = _re.sub(r"<[^>]+>", " ", clean)
    clean = _re.sub(
        r"\[(?:cackles?|chuckles?|cold\s*chuckle|arrogant\s*scoff|"
        r"deep\s*subtle\s*laugh|dry\s*laugh|laughs?|giggles?|sighs?|"
        r"whispers?|pause|beat|silence)[^\]]*\]",
        " ... ",
        clean,
        flags=_re.IGNORECASE,
    )
    clean = _re.sub(r"\s{2,}", " ", clean).strip()
    return clean
def _split_script_into_act_chunks(script: str, n_acts: int) -> list[str]:
    """Split narration into n_acts sequential ~3–4 s spoken snippets (chronological)."""
    return _segment_script_into_act_snippets(script or "", n_acts)
def _build_master_mei_script_act_prompts(
    *,
    subject: str,
    script: str,
    n_acts: int,
    base_style: str,
    visual_dna: str,
    hook_env: str,
    master_style_anchor: "str | None" = None,
    reference_folder: "str | Path | None" = None,
    use_vision_style: bool = False,
) -> tuple[list[str], set[int], list[str], list[str]]:
    """
    Build N image prompts for Master Mei sequence reels.

    Returns ``(prompts, mei_appearance_slots, roles, negatives)``.
    Avatar.png attaches ONLY for ROLE A (Master) slots — never globally.
    """
    prompts, slots, roles, negatives = _mei_build_act_prompts(
        subject=subject,
        script=script,
        n_acts=n_acts,
        base_style=base_style,
        visual_dna=visual_dna or _MEI_DNA_FALLBACK,
        hook_env=hook_env,
        segment_fn=_split_script_into_act_chunks,
        master_style_anchor=master_style_anchor,
        reference_folder=reference_folder,
        use_vision_style=use_vision_style,
    )
    return prompts, slots, roles, negatives
def _build_reel_act_descriptors(subject: str, n_acts: int, page_id: str = "") -> list[str]:
    """Return n_acts scene-description strings (one per act, index 0 = Act 1).

    All shots use WIDE, AERIAL, or PANORAMIC framing so primary subjects remain
    recognisable and centrally composed within social-media safe zones.
    Extreme / ground-level close-ups are intentionally avoided; medium-wide
    framing is the minimum for any detail shot.
    """
    # master_mei uses _build_master_mei_script_act_prompts() for dynamic matching.
    if (page_id or "").lower() == "master_mei":
        return [
            f"MASTER MEI ACT {i + 1} — {subject}."
            for i in range(n_acts)
        ]

    _ARC: list[str] = [
        # 0 — Act 1: wide establishing anchor (used as fallback descriptor only)
        (
            f"WIDE ESTABLISHING PANORAMA — {subject}. "
            "Iconic ancient monument fully visible, wide aerial or eye-level panoramic view. "
            "Rim-lit stone edges, volumetric light shafts, deep atmospheric haze. "
            "Primary subject centrally framed, full-bleed, no borders, no frames."
        ),
        # 1 — Act 2: wide approach shot — site context visible
        (
            f"WIDE APPROACH SHOT — {subject}. "
            "Broad view of the ancient site as the camera advances — full structure visible, "
            "surrounding landscape and dramatic sky in frame. Amber rim light on stone edges, "
            "sense of impossible ancient scale. Full-bleed, no borders."
        ),
        # 2 — Act 3: medium-wide detail shot (NOT extreme close-up)
        (
            f"MEDIUM-WIDE CONSTRUCTION DETAIL — {subject}. "
            "Mid-range shot showing an anomalous section of the site: perfectly fitted stones, "
            "impossible precision cuts, or unexplained engineering features — with enough "
            "surrounding context to establish scale. Warm amber sidelight, no borders, full-bleed."
        ),
        # 3 — Act 4: wide interior — full chamber visible
        (
            f"WIDE INTERIOR CHAMBER — {subject}. "
            "Full-room view of a deep ancient chamber — carved walls, ceiling height, and "
            "floor all visible. Volumetric cold light shaft from above. "
            "Artefacts in situ, vast spatial depth. No borders, full-bleed."
        ),
        # 4 — Act 5: wide inscription wall — full panel visible
        (
            f"WIDE INSCRIPTION WALL — {subject}. "
            "Full-panel view of an ancient wall or monument face covered in glyphs, "
            "star maps, or mathematical geometry — the entire composition visible in frame. "
            "Amber fire illumination, deep shadow. No borders, full-bleed."
        ),
        # 5 — Act 6: wide contextual artefact in situ
        (
            f"WIDE CONTEXTUAL ARTEFACT SHOT — {subject}. "
            "An alien or advanced object placed naturally within the ancient environment, "
            "full surroundings visible to convey impossible era-context. "
            "Dramatic single-source directional light, wide framing. Full-bleed."
        ),
        # 6 — Act 7: human scale comparison
        (
            f"HUMAN SCALE COMPARISON — {subject}. "
            "Wide shot: lone human silhouette dwarfed by a towering megalithic structure. "
            "Emphasises impossible construction scale. Rim-lit figure against dramatic sky. "
            "Centrally composed, full-bleed."
        ),
        # 7 — Act 8: wide night stellar alignment
        (
            f"WIDE NIGHT STELLAR ALIGNMENT — {subject}. "
            "Exterior wide shot at night — full ancient structure perfectly aligned with "
            "a constellation or celestial arc overhead. Deep indigo sky, Milky Way visible, "
            "cool moonlit stone edges. Cosmic and vast. Full-bleed."
        ),
        # 8 — Act 9: high-altitude aerial geometry
        (
            f"HIGH-ALTITUDE AERIAL GEOMETRY — {subject}. "
            "Top-down or near-vertical aerial view exposing perfect geometric layout, "
            "alignment grid, or civilisation-scale construction pattern. "
            "Golden-hour light, epic landscape. Full-bleed."
        ),
        # 9 — Act 10: sweeping panoramic revelation
        (
            f"SWEEPING PANORAMIC REVELATION — {subject}. "
            "Final wide cinematic panorama — the full mystery in one frame. "
            "Amber dust haze, otherworldly volumetric light, dramatic rim lighting, "
            "hauntingly unresolved. Epic full-bleed, no borders, no frames."
        ),
    ]
    # Build descriptor list for all n_acts; cycle if more than len(_ARC)
    return [_ARC[i % len(_ARC)] for i in range(n_acts)]


