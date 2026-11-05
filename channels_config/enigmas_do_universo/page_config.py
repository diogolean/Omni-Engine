# -*- coding: utf-8 -*-
"""Enigmas do Universo — page-level configuration.

Pin channel for astronomy mysteries. Photographs come from the shared
free-image chain (Wikimedia, Openverse, NASA, and the other keyless
providers) before any generator. The brand name is Portuguese. Pin copy
is English, the same language as the other factory channels.

There is no site yet. Publishing stays off until a URL exists.
"""
from __future__ import annotations

PAGE_ID: str = "enigmas_do_universo"
PAGE_DISPLAY_NAME: str = "Enigmas do Universo"

PINTEREST_BOARD_NAME: str = "Enigmas do Universo"
PINTEREST_BOARD_ID: str = ""

CONTENT_NICHE: str = (
    "astronomy mysteries, unexplained sky observations, deep-space photographs, "
    "and the open questions behind real NASA and observatory images"
)

DEFAULT_AVATAR_MODE: str = "OFF"
DEFAULT_FORMAT: str = "IMAGE_BACKGROUND"
IMAGE_ASPECT_RATIO: str = "2:3"
# Cards search the free libraries first. The chain already includes NASA.
IMAGE_SOURCE_POLICY: str = "free_first"
USES_AVATAR_REFERENCE: bool = False

COST_TIER: str = "nano"
ENABLE_COST_TRACKING: bool = True
ECONOMIC_BRAIN_MODE: bool = True

ATMOSPHERE_STYLE: str = (
    "Documentary photograph of a real astronomical subject: a nebula, galaxy, "
    "planet, moon, or spacecraft image with natural color and ordinary detail. "
    "No illustration, no invented craft, no human subject."
)

ILLUSTRATION_STYLE: str = (
    "Observatory or spacecraft photograph. Real sky, real instrument, "
    "natural color. No diagram, no painting, no science-fiction set."
)

# Pin photographs. Neutral documentary light. Pins do not use ATMOSPHERE_STYLE.
PIN_IMAGE_STYLE: str = (
    "Documentary photograph, natural color, realistic detail, "
    "a real astronomical subject."
)

# Pins stay at $0. A missing free photo is skipped and retried, never drawn.
PIN_PAID_IMAGE_FALLBACK: bool = False
# OpenRouter ":free" model. A failure uses the local template.
# Same free text model as Ancient Knowledge.
PIN_TEXT_MODEL: str = "openrouter:meta-llama/llama-3.3-70b-instruct:free"
# Hard cap for one pin. Any paid call is refused before the request.
PIN_SPEND_CAP_USD: float = 0.0

# This folder is a pin channel. It does not take the documentary reel path.
DOCUMENTARY_PIPELINE: bool = False

PROMPT_NEGATIVE_TERMS: list = [
    "illustration", "diagram", "infographic", "anime", "cartoon",
    "science fiction set", "alien portrait", "cyberpunk", "neon city",
    "human face", "logo", "watermark", "text overlay",
]

FONT_PATH: str = "Fonts/Montserrat/static/Montserrat-Bold.ttf"
FONT_SIZE_SCALE: float = 0.07
FONT_COLOR: tuple = (236, 242, 255)
TEXT_OUTLINE_WIDTH: int = 2

LOGO_SIZE_SCALE: float = 0.28
LOGO_POSITION: str = "bottom_center"

BRAND_GRADIENT: tuple = ((4, 8, 22), (10, 22, 48))
BRAND_ACCENT_COLORS: tuple = (
    (236, 242, 255),
    (186, 210, 245),
    (140, 176, 220),
    (220, 230, 255),
    (160, 190, 230),
)

TOPIC_POOL: list = [
    "Why the Moon's far side has almost no dark plains",
    "The Great Attractor and what is pulling the Local Group",
    "Tabby's star and the dimming no dust model has settled",
    "ʻOumuamua and the acceleration that was not a comet tail",
    "Fast radio bursts that repeat and still have no home galaxy we can point to",
    "The Hubble tension: two measurements of the universe's expansion that disagree",
    "The cold spot in the cosmic microwave background",
    "Saturn's north-polar hexagon and why the jet stays six-sided",
    "Europa's subsurface ocean and the plumes Hubble keeps arguing about",
    "The Wow! signal and the one frequency that never repeated",
    "Betelgeuse's great dimming and the dust the star threw off",
    "Voyager 1 beyond the heliopause and the plasma hum it still sends",
    "The missing baryons between the galaxies",
    "Dark matter and the rotation curves that need more mass than the stars",
    "The Pioneer anomaly and the sunward drift that took decades to explain",
    "PSR B1919+21, the first pulsar, and the signal Jocelyn Bell Burnell refused to call a civilization",
]

NICHE_DISCLAIMER: str = (
    "CHANNEL CONTEXT: Enigmas do Universo is an astronomy pin channel. "
    "The brand name stays Portuguese. The pin title, caption, and hook are English, "
    "the same language as the other factory channels. "
    "Open with a real observation (a NASA or observatory photograph, a named object, "
    "a measured anomaly) and then the question that observation still leaves open. "
    "Present unsettled ideas as unsettled. Use 'astronomers still debate', "
    "'one measurement shows', 'the photograph does not decide'. "
    "Never claim an alien visitation, a secret program, or a settled theory as fact. "
    "No hype words: unlock, discover, elevate, game-changer, harness, revolutionary, dive."
)
