import math

from utils.config import config, get_resolution_value


RETAIN_ORIGINS = frozenset({"whitelist", "hls"})


def channel_result_rejection(
        item: dict,
        *,
        retain_special: bool = False,
        supply: bool | None = None,
        filter_speed: bool | None = None,
        min_speed: float | None = None,
        resolution_speed_map: dict[str, float] | None = None,
        filter_resolution: bool | None = None,
        min_resolution: int | None = None,
        max_resolution: int | None = None,
) -> str | None:
    """Return the reason a measured channel result is not playable."""
    if retain_special and item.get("origin") in RETAIN_ORIGINS:
        return None

    speed = item.get("speed")
    delay = item.get("delay")
    if (
        isinstance(speed, bool)
        or not isinstance(speed, (int, float))
        or speed <= 0
        or not math.isfinite(speed)
        or isinstance(delay, bool)
        or not isinstance(delay, (int, float))
        or delay < 0
        or not math.isfinite(delay)
    ):
        return "unreachable"

    supply = config.open_supply if supply is None else supply
    if supply:
        return None

    filter_speed = config.open_filter_speed if filter_speed is None else filter_speed
    min_speed = config.min_speed if min_speed is None else min_speed
    if resolution_speed_map is None:
        resolution_speed_map = config.resolution_speed_map
    resolution = item.get("resolution")
    if filter_speed and speed < resolution_speed_map.get(resolution, min_speed):
        return "filtered_speed"

    filter_resolution = (
        config.open_filter_resolution
        if filter_resolution is None
        else filter_resolution
    )
    if filter_resolution and resolution:
        min_resolution = (
            config.min_resolution_value
            if min_resolution is None
            else min_resolution
        )
        max_resolution = (
            config.max_resolution_value
            if max_resolution is None
            else max_resolution
        )
        resolution_value = get_resolution_value(resolution)
        if (
            resolution_value <= 0
            or resolution_value < min_resolution
            or resolution_value > max_resolution
        ):
            return "filtered_resolution"

    return None


def is_channel_result_valid(item: dict, **kwargs) -> bool:
    """Return whether a measured channel result satisfies the active rules."""
    return channel_result_rejection(item, **kwargs) is None
