"""Shape-preserving interpolation: smoother strokes without new peaks or changed samples."""

import math


def smooth_points(points: list[tuple[float, float]], resolution: float = 1.0):
    # Coincident timestamps may come from manually queried data; keep the last value.
    unique = {}
    for x, y in points:
        unique[x] = y
    points = sorted(unique.items())
    if len(points) < 2:
        return points
    widths = [b[0] - a[0] for a, b in zip(points, points[1:])]
    slopes = [(b[1] - a[1]) / width for a, b, width in zip(points, points[1:], widths)]
    tangents = [slopes[0]]
    for index in range(1, len(points) - 1):
        before, after = slopes[index - 1], slopes[index]
        if before * after <= 0:
            tangents.append(0.0)
        else:
            w1 = 2 * widths[index] + widths[index - 1]
            w2 = widths[index] + 2 * widths[index - 1]
            tangents.append((w1 + w2) / (w1 / before + w2 / after))
    tangents.append(slopes[-1])
    result = [points[0]]
    for index, (start, end) in enumerate(zip(points, points[1:])):
        width = widths[index]
        steps = max(2, math.ceil(width / max(resolution, 0.1)))
        for step in range(1, steps + 1):
            t = step / steps
            y = (
                (2 * t**3 - 3 * t**2 + 1) * start[1]
                + (t**3 - 2 * t**2 + t) * width * tangents[index]
                + (-2 * t**3 + 3 * t**2) * end[1]
                + (t**3 - t**2) * width * tangents[index + 1]
            )
            # Numerical guard only; monotone tangents already bound the interval.
            y = max(min(start[1], end[1]), min(max(start[1], end[1]), y))
            result.append((start[0] + t * width, y))
    return result
