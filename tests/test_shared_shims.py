"""ADR 0008 M2: old import paths are the shared modules, not copies."""
from __future__ import annotations

import modules.reel_artefact.net_retry as net_retry
import shared.ffmpeg as ffmpeg
import shared.paths as paths
import shared.retry as retry
import utils.pipeline_paths as pipeline_paths
import utils.process_tools as process_tools


def test_path_and_process_shims_are_the_shared_modules() -> None:
    assert pipeline_paths is paths
    assert process_tools is ffmpeg
    assert net_retry is retry
    assert pipeline_paths.repo_root() == paths.factory_root()
    assert process_tools.kill_descendant_ffmpeg is ffmpeg.kill_descendant_ffmpeg
    assert net_retry.retry_sleep is retry.retry_sleep
    assert net_retry.is_network_error is retry.is_network_error
