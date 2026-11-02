"""Conservative information gain in integer outer intervals, never confidence."""

from src.domain.bo3_inference.models import PointRange, RangeReduction


def compare_ranges(baseline: PointRange, current: PointRange) -> RangeReduction | None:
    """Count eliminated integers only for valid, nested outer intervals."""
    if not baseline.minimum <= current.minimum <= current.maximum <= baseline.maximum:
        return None
    initial_count = baseline.maximum - baseline.minimum + 1
    current_count = current.maximum - current.minimum + 1
    removed = initial_count - current_count
    return RangeReduction(
        baseline=PointRange(minimum=baseline.minimum, maximum=baseline.maximum),
        current=PointRange(minimum=current.minimum, maximum=current.maximum),
        removed_integer_values=removed,
        reduction_percent=round(100 * removed / initial_count, 1),
    )
