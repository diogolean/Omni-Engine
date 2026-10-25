# -*- coding: utf-8 -*-
"""Channel behaviour comes from page_config flags, not channel-name branches."""
from __future__ import annotations

from pathlib import Path

from production.formats.lofi.config import (
    CHANNEL_ASSEMBLY,
    MODULE_PAGE_GATES,
    VALID_PAGES as LOFI_PAGES,
    channel_assembly_cfg,
)
from production.media.brand_composer import _brand_gradient
from production.pipeline.channel_context import VALID_PAGES
from production.pipeline.channel_flags import (
    compose_store_mounts,
    discover_pages,
    flag,
    pages_with,
    value,
    vm_mkdir_relpaths,
)
from production.pipeline.interfaces.factory import ISOLATED_CHANNEL_IDS, ChannelFactory
from production.providers.image.wan_reel_production import (
    WAN_REEL_PAGE_ID,
    assert_wan_reel_page,
)

_ROOT = Path(__file__).resolve().parents[1]
_HISTORICAL = (
    "anna_protocol",
    "master_mei",
    "wonder_feed",
    "down_dirty",
    "ancient_knowledge",
    "momma_circle",
    "principles_of_wealth_finance_economics",
    "endless_summer_paradise",
)


def test_discover_pages_matches_the_historical_order() -> None:
    assert discover_pages() == _HISTORICAL
    assert VALID_PAGES == _HISTORICAL


def test_pipeline_flags_stay_on_the_channels_that_had_the_name_branch() -> None:
    assert pages_with("DOCUMENTARY_PIPELINE") == ("ancient_knowledge",)
    assert pages_with("MEI_PIPELINE") == ("master_mei",)
    assert pages_with("GRAPHITE_STYLE_LOCK") == ("wonder_feed",)
    assert pages_with("WAN_REEL") == ("ancient_knowledge",)
    assert pages_with("REFERENCE_REEL_LOCK") == ("momma_circle",)
    assert pages_with("NATURAL_DRAW_LOCK") == ("anna_protocol",)
    assert pages_with("ALLOWS_CTA_CAPTION_IMAGE") == ("anna_protocol",)
    assert set(pages_with("LOFI_ENABLED")) == {"wonder_feed", "momma_circle"}
    assert set(pages_with("POOL_TOPIC_LOCK")) == {"wonder_feed", "master_mei"}
    assert pages_with("VM_STATE_MOUNT") == ("ancient_knowledge",)
    assert set(pages_with("VM_ENSURE_CHANNEL_DIR")) == {
        "ancient_knowledge",
        "anna_protocol",
    }


def test_isolation_is_the_adapter_file_not_a_name_list() -> None:
    assert ISOLATED_CHANNEL_IDS == frozenset(
        {"ancient_knowledge", "endless_summer_paradise"}
    )
    assert ChannelFactory.is_isolated("ancient_knowledge")
    assert ChannelFactory.is_isolated("endless_summer_paradise")
    assert not ChannelFactory.is_isolated("wonder_feed")
    assert not ChannelFactory.is_isolated("master_mei")


def test_brand_gradients_match_the_old_palettes() -> None:
    assert _brand_gradient("wonder_feed") == ((10, 18, 35), (22, 38, 62))
    assert _brand_gradient("anna_protocol") == ((14, 10, 6), (30, 22, 12))
    assert _brand_gradient("down_dirty") == ((8, 18, 6), (18, 34, 12))
    assert _brand_gradient("master_mei") == ((10, 10, 10), (28, 28, 28))
    assert _brand_gradient("") == ((10, 10, 10), (28, 28, 28))


def test_vm_paths_and_compose_mounts_follow_the_flags() -> None:
    assert vm_mkdir_relpaths() == (
        "channels_config/anna_protocol",
        "channels_config/ancient_knowledge",
        "state/ancient_knowledge/store",
    )
    mounts = compose_store_mounts()
    assert mounts == (
        "./state/ancient_knowledge/store:"
        "/opt/omni-engine/channels_config/ancient_knowledge/store",
    )
    compose = (_ROOT / "deploy" / "compose" / "docker-compose.yml").read_text(
        encoding="utf-8"
    )
    for mount in mounts:
        assert compose.count(mount) == 2
    workflow = (_ROOT / ".github" / "workflows" / "deploy.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m production.pipeline.channel_flags --vm-mkdir" in workflow
    remote = (_ROOT / "deploy" / "vm" / "remote-deploy.sh").read_text(encoding="utf-8")
    assert "CHANNEL_DIRS" in remote
    assert "channels_config/ancient_knowledge" not in remote
    assert "compose -f" not in remote
    assert "--file" not in remote


def test_lofi_tables_come_from_the_channel_folders() -> None:
    assert LOFI_PAGES == frozenset({"wonder_feed", "momma_circle"})
    assert MODULE_PAGE_GATES["relationship"] == LOFI_PAGES
    assert MODULE_PAGE_GATES["parenting"] == LOFI_PAGES
    momma = CHANNEL_ASSEMBLY["momma_circle"]
    assert momma["logo_scale"] == 0.18
    assert momma["logo_bottom_px"] == 252
    assert momma["logo_size_multiplier"] == 0.337
    assert momma["trim_logo_transparent_padding"] is True
    wonder = channel_assembly_cfg("wonder_feed")
    assert wonder["logo_scale"] == 0.224
    assert wonder["logo_bottom_px"] == 389
    assert "trim_logo_transparent_padding" not in wonder
    assert channel_assembly_cfg("not-a-channel")["logo_scale"] == 0.224


def test_wan_reel_names_the_channels_that_opt_in() -> None:
    assert WAN_REEL_PAGE_ID == "ancient_knowledge"
    assert_wan_reel_page("ancient_knowledge")
    try:
        assert_wan_reel_page("wonder_feed")
    except SystemExit as exc:
        assert "ancient_knowledge" in str(exc)
    else:
        raise AssertionError("wonder_feed must not run WAN_REEL")


def test_youtube_rules_are_flags() -> None:
    from distribution.publishers.api.youtube_upload import (
        _default_tags_for_page,
        _expected_channel_hint,
        _playlist_when_description_empty,
        _resolve_page_playlist_meta,
        sanitize_youtube_title,
    )

    title, _desc = _resolve_page_playlist_meta("ancient_knowledge")
    assert title == "Ancient Mysteries & Forbidden History"
    mei_title, mei_desc = _resolve_page_playlist_meta("master_mei")
    assert mei_title == "Stoic Financial Freedom & Wealth Mindset"
    assert "Protocol for Liberation" in mei_desc
    empty = _playlist_when_description_empty("master_mei", mei_title)
    assert "financial clarity" in empty
    assert "Protocol for Liberation" not in empty
    assert _playlist_when_description_empty("wonder_feed", "Wonder").startswith(
        "Official playlist:"
    )
    assert _expected_channel_hint("master_mei") == "Master Mei"
    assert _expected_channel_hint("ancient_knowledge") == "Ancient Knowledge"
    assert _expected_channel_hint("principles_of_wealth_finance_economics") == (
        "Principles of Wealth"
    )
    tags = _default_tags_for_page("ancient_knowledge")
    assert "forbidden history" in tags
    assert _default_tags_for_page("down_dirty") == ["shorts"]
    wealth, _hashes = sanitize_youtube_title(
        "Markets #shorts | Principles of Wealth",
        "principles_of_wealth_finance_economics",
    )
    assert "#shorts" in wealth
    mei, _ = sanitize_youtube_title("MASTER MEI | Mind Control holds the line", "master_mei")
    assert "MASTER" not in mei
    assert "Mind Control" not in mei
    assert flag("master_mei", "YOUTUBE_PLAYLIST_FORCE_DESCRIPTION")
    assert value("master_mei", "AMBIENT_MUSIC_STYLE") == "warrior"
    assert value("ancient_knowledge", "NARRATIVE_MODE") == "investigative"


def test_voice_pin_grounding_and_path_flags_stay_exclusive() -> None:
    assert pages_with("VISUAL_GROUNDING") == ("ancient_knowledge",)
    assert pages_with("ANGLE_MEMORY") == ("ancient_knowledge",)
    assert pages_with("ARTICLE_LINK_REQUIRED") == ("ancient_knowledge",)
    assert pages_with("LEGACY_SHARED_LEDGER") == ("anna_protocol",)
    assert pages_with("LEGACY_MEDIA_DNA") == ("anna_protocol",)
    assert pages_with("USE_APPROVED_CTA") == ("master_mei",)
    assert set(pages_with("USE_NARRATION_WORD_CAP")) == {
        "master_mei",
        "ancient_knowledge",
    }
    assert value("ancient_knowledge", "TTS_SPEED_LOCK") == 1.0
    assert value("ancient_knowledge", "TTS_NETWORK_RETRIES") == 3
    workbook = value("wonder_feed", "QUOTES_WORKBOOK")
    assert workbook.endswith(
        "Quotes_reference/@REDES REF SOURCE FONTE.xlsx"
    )
    assert pages_with("QUOTES_WORKBOOK") == ("wonder_feed",)


def test_engine_source_has_no_channel_id_literals() -> None:
    """String and f-string tokens in the engine must not name a channel.

    The historical order lives in ``shared/channel_flags.py``. Identifiers
    such as ``generate_master_mei_soundscape`` are not literals.
    """
    import io
    import re
    import tokenize

    names = "|".join(re.escape(name) for name in _HISTORICAL)
    word = re.compile(rf"\b(?:{names})\b")
    files: list[Path] = []
    files.extend((_ROOT / "production" / "pipeline").rglob("*.py"))
    files.append(_ROOT / "production" / "writers" / "caption_engine.py")
    files.extend((_ROOT / "production" / "formats" / "pins").rglob("*.py"))
    files.extend(
        (_ROOT / "production" / "formats" / "economic_reel" / "grounding").rglob(
            "*.py"
        )
    )
    files.append(_ROOT / "shared" / "paths.py")
    files.append(_ROOT / "channels_config" / "shared" / "persona_bind.py")
    literal_types = {tokenize.STRING, tokenize.FSTRING_MIDDLE}
    hits: list[str] = []
    for path in files:
        tokens = tokenize.tokenize(io.BytesIO(path.read_bytes()).readline)
        for tok in tokens:
            if tok.type not in literal_types or not word.search(tok.string):
                continue
            rel = path.relative_to(_ROOT)
            hits.append(f"{rel}:{tok.start[0]}: {tok.string[:120]}")
    assert hits == []


def test_cli_help_still_names_the_flag_channels(capsys) -> None:
    from production.pipeline.cli_parser import parse_cli_args

    try:
        parse_cli_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "Default: anna_protocol." in help_text
    assert "Auto-on for ancient_knowledge static image posts." in help_text
    assert "CTA_CAPTION_IMAGE: anna_protocol only." in help_text
    assert "WAN_REEL: ancient_knowledge only." in help_text
    assert "Designed for momma_circle." in help_text
    assert "parenting for wonder_feed + momma_circle." in help_text
    assert "ECONOMIC_REEL on wonder_feed" in help_text
    assert "(e.g. ancient_knowledge ~80)." in help_text
    assert "DEFAULT_AVATAR_MODE" in help_text
    from production.pipeline.channel_flags import sole_page

    page = sole_page("ALLOWS_CTA_CAPTION_IMAGE")
    assert (
        f"CTA_CAPTION_IMAGE is only available for --page {page}. "
        f"Example: python main.py --page {page} "
        "--post-type CTA_CAPTION_IMAGE --quantity 1"
    ) == (
        "CTA_CAPTION_IMAGE is only available for --page anna_protocol. "
        "Example: python main.py --page anna_protocol "
        "--post-type CTA_CAPTION_IMAGE --quantity 1"
    )
