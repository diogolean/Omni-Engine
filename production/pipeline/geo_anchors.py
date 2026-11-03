# -*- coding: utf-8 -*-
"""Identity-only place anchors for Ancient Knowledge still prompts.

This table is data. Import it from here. It does not belong on
``engine.runtime``, which loads the media stack.
"""
from __future__ import annotations

# Known geographic/visual anchors mapped to camera-perspective image directives.
# _GEO_ANCHORS returns IDENTITY-ONLY entity strings (place, material, era, era-
# specific culture keywords). Camera framing, angle, and lighting were removed
# 2026-08-15 — the per-act shot-pool + lighting-pool + subject-pool now own
# framing/mood. Templates that used to say "wide aerial panoramic view, …" or
# "wide interior shot, … dramatic divine light beams" were overriding the per-
# act shot assignment and re-anchoring FLUX to a centered-doorway/monument
# default regardless of what the planner asked for.
_GEO_ANCHORS: list[tuple[tuple[str, ...], str]] = [
    (("nazca", "geoglyph", "nazca lines"),
     "Nazca Lines geoglyphs, Nazca desert Peru, pale sand plateau"),
    (("pyramid", "giza", "great pyramid", "khufu"),
     "Great Pyramid of Giza, Giza Plateau Egypt, limestone masonry, "
     "old-kingdom pharaonic era"),
    (("sphinx",),
     "Great Sphinx of Giza, Giza Plateau Egypt, weathered limestone, "
     "pharaonic era"),
    (("baalbek", "trilithon"),
     "Baalbek temple complex Lebanon, thousand-tonne megalithic "
     "trilithon blocks, roman-era platform on pre-roman substructure"),
    (("easter island", "moai", "rapa nui"),
     "Easter Island moai statues, Rapa Nui volcanic tuff, Pacific "
     "coastline Polynesia"),
    (("stonehenge",),
     "Stonehenge stone circle, Salisbury Plain England, sarsen and "
     "bluestone megaliths, neolithic era"),
    (("gobekli", "göbekli", "gobeklitepe"),
     "Göbekli Tepe T-shaped stone pillars, southeastern Turkey, "
     "carved limestone, ~11 000 BCE hunter-gatherer era"),
    (("puma punku", "pumapunku", "tiahuanaco", "tiwanaku"),
     "Puma Punku megalithic site, Bolivian Altiplano, precision-cut "
     "andesite and diorite blocks with H-cut joints"),
    (("atlantis",),
     "Atlantis mythology, submerged ancient civilisation setting"),
    (("tartaria", "tartarian"),
     "Tartaria mythology, grand pre-industrial stone architecture, "
     "eurasian steppe context"),
    (("sumerian", "sumer", "mesopotamia", "ur"),
     "Ancient Sumer / Mesopotamia, mud-brick ziggurats, Tigris-"
     "Euphrates plain, Bronze-Age era"),
    (("angkor", "angkor wat"),
     "Angkor Wat temple complex Cambodia, sandstone Khmer masonry, "
     "surrounding jungle canopy"),
    (("machu picchu",),
     "Machu Picchu Inca citadel, Peruvian Andes, andesite ashlar "
     "masonry, mountain terraces"),
    (("teotihuacan", "teotihuacán"),
     "Teotihuacan Pyramid of the Sun, Avenue of the Dead alignment, "
     "central Mexican highlands, pre-Aztec era"),
    (("dendera", "denderah", "hathor"),
     "Dendera temple complex Egypt, Hathor sanctuary, ptolemaic-era "
     "sandstone reliefs, celestial zodiac ceiling motif"),
    (("dwarka",),
     "Dwarka submerged city ruins, Gulf of Cambay India, sunken "
     "stone masonry"),
    (("yonaguni",),
     "Yonaguni Monument, Ryukyu Islands Japan, submerged terraced "
     "rock formation"),
    (("caral",),
     "Caral pyramid complex, Supe Valley Peru, sunken plazas, "
     "pre-Ceramic Andean era"),
    (("pompeii", "vesuvius"),
     "Pompeii Roman city, Mount Vesuvius volcanic ash, preserved "
     "roman streets and frescoes"),
    (("santorini", "thera", "akrotiri"),
     "Akrotiri Minoan site, Thera / Santorini caldera, volcanic "
     "pumice preservation, Bronze-Age Aegean"),
    # ── Artifact / thematic anchors (identity only, no framing/lighting) ────
    (("crystal skull", "crystal skulls"),
     "carved quartz-crystal skull artefact"),
    (("ark of the covenant", "ark of covenant", "holy ark"),
     "gold-plated Ark of the Covenant artefact, biblical era"),
    (("shroud of turin", "shroud"),
     "Shroud of Turin linen relic with faint human image"),
    (("antikythera", "antikythera mechanism"),
     "Antikythera Mechanism, corroded bronze differential gear "
     "device, hellenistic-era artefact"),
    (("lost technology", "precision finish", "machined", "laser cut"),
     "precision-machined ancient stone or metal artefact with "
     "impossible geometric tolerances for its era"),
    (("oopart", "out of place artefact", "out-of-place artifact", "oopartz"),
     "mysterious out-of-place ancient object, museum-provenance "
     "artefact"),
    (("ancient astronaut", "ancient aliens", "extraterrestrial", "alien contact"),
     "ancient stone reliefs and glyphs depicting humanoid figures "
     "in helmet-like headgear or craft-like vessels"),
    (("lost city", "sunken city", "underwater ruins"),
     "submerged ancient stone city ruins on the ocean floor"),
    (("book of enoch", "enoch", "watchers", "nephilim"),
     "Book of Enoch imagery, ancient manuscript pages and giant "
     "humanoid figures"),
    (("free energy", "tesla", "ancient electricity", "baghdad battery", "vimana"),
     "ancient device suggesting pre-industrial electrical or "
     "advanced technology, museum artefact context"),
    (("dogon", "sirius mystery"),
     "Dogon people of Mali, Bandiagara escarpment, cliff-face "
     "villages, Sirius / Po Tolo cosmological tradition"),
    (("younger dryas", "cataclysm", "comet impact", "black mat"),
     "Younger Dryas impact hypothesis context, permafrost mammoth "
     "fossils, nanodiamond black-mat sediment layer"),
    (("cave painting", "lascaux", "chauvet", "altamira", "prehistoric art"),
     "prehistoric cave-wall pigment paintings, deep limestone "
     "cavern interior, palaeolithic era"),
]

