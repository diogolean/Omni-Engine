# -*- coding: utf-8 -*-
"""
pinterest_engine
----------------
Agnostic multi-channel Pinterest Sales & Recycling Engine.

Brand CTAs, URLs, and persona prompts live only in each channel's
``channels_config/<channel_id>/config.json`` (or channel ``.env``) — never in core code.

Pin production (channel config, copy, photos, articles, the image
transformer, Flux) lives in ``production.formats.pins``. Those names, and
the publisher, scheduler, ledger, inventory, library source, and Facebook
queue, remain importable from this package as shims. Root ``config.py``
is unchanged.
"""
