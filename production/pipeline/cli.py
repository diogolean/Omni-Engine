# -*- coding: utf-8 -*-
"""CLI entry. Flags are built in cli_parser; the run sequence is cli_run."""
from __future__ import annotations

from production.pipeline.cli_parser import _together_cli_image_model, parse_cli_args
from production.pipeline.cli_run import run_parsed_cli

def cli() -> None:
    run_parsed_cli(parse_cli_args())
