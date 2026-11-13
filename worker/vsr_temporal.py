"""Interpolate enhanced anchors on the unchanged source presentation clock."""
from fractions import Fraction


def source_pairs(frames, first_slot, end_slot, stride):
    """Keep source-rate slots; sample expensive enhancement work, never the output clock."""
    if not 0 <= first_slot <= end_slot:
        raise ValueError('Invalid source frame range')
    frames = iter(frames)
    left = next(frames, None)
    while left is not None and left[1] < end_slot:
        step = stride()
        if not isinstance(step, int) or not 1 <= step <= 4:
            raise ValueError('Enhancement sampling stride must be 1 through 4')
        right = left
        for current in frames:
            right = current
            if current[1] >= min(left[1] + step, end_slot - 1):
                break
        a, b = left[1], right[1]
        if b == a:
            for slot in range(max(first_slot, a), end_slot):
                yield left[0], left[0], Fraction(0), slot, a, a
            return
        for slot in range(max(first_slot, a), min(b, end_slot)):
            yield left[0], right[0], Fraction(slot - a, b - a), slot, a, b
        left = right
