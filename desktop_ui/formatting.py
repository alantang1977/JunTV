def format_bandwidth(bits_per_second) -> str:
    """Format a bandwidth value from the RTMP status feed for display."""
    try:
        kilobits_per_second = max(0.0, float(bits_per_second)) / 1000
    except (TypeError, ValueError):
        kilobits_per_second = 0.0
    if kilobits_per_second >= 1000:
        return f"{kilobits_per_second / 1000:.1f} Mbit/s"
    return f"{kilobits_per_second:.1f} Kbit/s"
