# -*- coding: utf-8 -*-
"""
pinterest_engine
----------------
Agnostic multi-channel Pinterest Sales & Recycling Engine.

Brand CTAs, URLs, and persona prompts live only in each channel's
``channels_config/<channel_id>/config.json`` (or channel ``.env``) — never in core code.

Modules that still live here are the pin-production pieces (channel
config, copy, photos, articles, the image transformer, Flux). The publisher,
scheduler, ledger, inventory, library source, and Facebook queue moved to
``distribution`` and remain importable from this package as shims.
"""
