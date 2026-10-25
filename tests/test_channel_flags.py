# -*- coding: utf-8 -*-
"""Channel behaviour comes from page_config flags, not channel-name branches."""
from __future__ import annotations

import re
from pathlib import Path

from production.formats.lofi.config import (
    CHANNEL_ASSEMBLY,
    MODULE_PAGE_GATES,
    VALID_PAGES as LOFI_PAGES,
    channel_assembly_cfg,
)
from production.media.brand_composer import _brand_accents, _brand_gradient
from production.pipeline.channel_context import VALID_PAGES
from production.pipeline.channel_flags import (
    coalesce_page,
    compose_store_mounts,
    default_page,
    discover_channel_ids,
    discover_pages,
    flag,
    pages_with,
    sole_page,
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
    """Flag order stays historical. The CLI list is the sorted folder walk."""
    assert discover_pages() == _HISTORICAL
    assert set(VALID_PAGES) == set(_HISTORICAL)
    assert list(VALID_PAGES) == sorted(VALID_PAGES)


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


_CHANNEL_PATH_RE = r"^(channels_config|state)/[a-z0-9_]+(/store)?$"


def _assert_channel_dir_guards(workflow: str, remote: str) -> None:
    deploy = workflow.split("\n  deploy:\n", 1)[1]
    setup = deploy.index("uses: actions/setup-python@v5")
    pinned = deploy.index('python-version: "3.12"')
    compute = deploy.index("python -m production.pipeline.channel_flags --vm-mkdir")
    empty = deploy.index("Refusing deploy. CHANNEL_DIRS is empty.")
    pattern = deploy.index(_CHANNEL_PATH_RE)
    assert setup < pinned < compute < empty < pattern
    assert "exit 1" in deploy[empty:pattern]
    assert remote.index(_CHANNEL_PATH_RE) < remote.index('mkdir -p "$rel"')
    assert "mkdir -p ${CHANNEL_DIRS}" not in remote


def test_vm_paths_and_compose_mounts_follow_the_flags() -> None:
    paths = vm_mkdir_relpaths()
    assert paths == (
        "channels_config/anna_protocol",
        "channels_config/ancient_knowledge",
        "state/ancient_knowledge/store",
    )
    allowed = re.compile(_CHANNEL_PATH_RE)
    assert all(allowed.fullmatch(rel) for rel in paths)
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
    _assert_channel_dir_guards(workflow, remote)


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


# Format modules that belong to one channel. The guard does not scan these.
_ENGINE_ALLOWLIST = (
    Path("production/formats/mei"),
    Path("production/writers/mei_narrative.py"),
    Path("production/providers/image/visual_compiler.py"),
)


def _allowlisted(rel: Path) -> bool:
    return any(rel == item or item in rel.parents for item in _ENGINE_ALLOWLIST)


def test_engine_source_has_no_channel_id_literals() -> None:
    """String and f-string tokens in production/ must not name a channel.

    The historical order lives in ``shared/channel_flags.py``. Identifiers
    such as ``generate_master_mei_soundscape`` are not literals. One-channel
    format modules are the explicit allowlist above.
    """
    import io
    import re
    import tokenize

    names = "|".join(re.escape(name) for name in _HISTORICAL)
    word = re.compile(rf"\b(?:{names})\b")
    literal_types = {tokenize.STRING, tokenize.FSTRING_MIDDLE}
    hits: list[str] = []
    files = list((_ROOT / "production").rglob("*.py"))
    files.append(_ROOT / "shared" / "paths.py")
    files.append(_ROOT / "channels_config" / "shared" / "persona_bind.py")
    for path in files:
        rel = path.relative_to(_ROOT)
        if _allowlisted(rel):
            continue
        tokens = tokenize.tokenize(io.BytesIO(path.read_bytes()).readline)
        for tok in tokens:
            if tok.type not in literal_types or not word.search(tok.string):
                continue
            hits.append(f"{rel}:{tok.start[0]}: {tok.string[:120]}")
    assert hits == []


def test_discover_channel_ids_matches_pages() -> None:
    assert discover_channel_ids() == discover_pages()
    assert discover_channel_ids()[0] == default_page()


def test_coalesce_page_keeps_a_named_channel() -> None:
    assert coalesce_page("", "DOCUMENTARY_PIPELINE") == "ancient_knowledge"
    assert coalesce_page("  ", "DOCUMENTARY_PIPELINE") == "ancient_knowledge"
    assert coalesce_page("wonder_feed", "DOCUMENTARY_PIPELINE") == "wonder_feed"
    assert coalesce_page(None, "DOCUMENTARY_PIPELINE") == "ancient_knowledge"


def test_brand_accents_match_the_old_palettes() -> None:
    assert _brand_accents("wonder_feed") == [
        (180, 210, 240),
        (230, 180, 195),
        (245, 230, 200),
        (200, 220, 245),
        (240, 200, 215),
    ]
    assert _brand_accents("anna_protocol") == [
        (255, 220, 100),
        (255, 235, 160),
        (255, 200, 80),
        (240, 215, 140),
        (255, 230, 130),
    ]
    assert _brand_accents("down_dirty") == [
        (210, 255, 210),
        (255, 240, 180),
        (200, 245, 230),
        (240, 210, 150),
    ]
    assert _brand_accents("master_mei")[0] == (255, 220, 100)
    assert _brand_accents("")[0] == (255, 220, 100)


def test_lofi_assets_and_wealth_catalog_follow_flags() -> None:
    from production.formats.lofi.config import BGM_DIR_REL, DUST_OVERLAY_REL
    from production.formats.wealth.catalog import CHANNEL_ID

    page = sole_page("LOFI_ASSEMBLY_FALLBACK")
    assert DUST_OVERLAY_REL == value(page, "LOFI_DUST_OVERLAY")
    assert BGM_DIR_REL == value(page, "LOFI_BGM_DIR")
    assert CHANNEL_ID == sole_page("WEALTH_CATALOG")
    assert CHANNEL_ID == "principles_of_wealth_finance_economics"


def test_cli_help_derives_channels_from_discovery(capsys) -> None:
    from production.pipeline.cli_parser import parse_cli_args

    try:
        parse_cli_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    ids = discover_channel_ids()
    assert f"Options: {', '.join(ids)}." in help_text
    assert f"Default: {ids[0]}." in help_text
    assert "Auto-on for static image posts on the documentary pipeline." in help_text
    assert "CTA_CAPTION_IMAGE: channels that allow a CTA caption image." in help_text
    assert "WAN_REEL: channels that opt into WAN." in help_text
    assert "Designed for channels with a reference-reel lock." in help_text
    assert "parenting for channels that enable lofi." in help_text
    assert "ECONOMIC_REEL on the graphite lock" in help_text
    assert "Each channel supplies its own default when omitted." in help_text
    assert "DEFAULT_AVATAR_MODE" in help_text
    assert "ancient_knowledge only" not in help_text
    assert "anna_protocol only" not in help_text

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
