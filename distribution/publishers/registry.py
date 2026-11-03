"""Pick the publisher for a platform name."""

from __future__ import annotations

from distribution.publishers.base import Publisher


def publisher_for(platform: str, **kwargs) -> Publisher:
    name = platform.strip().lower()
    if name == "pinterest":
        from distribution.publishers.api.pinterest import PinterestAccountPublisher

        return PinterestAccountPublisher(**kwargs)
    if name == "youtube":
        from distribution.publishers.api.youtube import YouTubeAccountPublisher

        return YouTubeAccountPublisher(**kwargs)
    if name == "facebook":
        raise NotImplementedError(
            "Facebook stays on the Playwright / Post Planner path "
            "(distribution.publishers.browser.facebook_scheduler). "
            "brandings.yaml can mark it active later; there is no API publisher yet."
        )
    raise KeyError(f"No publisher registered for platform {platform!r}")
