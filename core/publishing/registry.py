"""Pick the publisher for a platform name."""

from __future__ import annotations

from core.publishing.publisher import Publisher


def publisher_for(platform: str, **kwargs) -> Publisher:
    name = platform.strip().lower()
    if name == "pinterest":
        from core.publishing.adapters.pinterest import PinterestAccountPublisher

        return PinterestAccountPublisher(**kwargs)
    if name == "youtube":
        from core.publishing.adapters.youtube import YouTubeAccountPublisher

        return YouTubeAccountPublisher(**kwargs)
    if name == "facebook":
        raise NotImplementedError(
            "Facebook stays on the Playwright / Post Planner path "
            "(agents/posting/facebook_scheduler). brandings.yaml can mark it "
            "active later; there is no API publisher yet."
        )
    raise KeyError(f"No publisher registered for platform {platform!r}")
