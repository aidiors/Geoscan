"""Independent adjacent-photo overlap diagnostic for saved free-geometry routes.

Area intersection of consecutive nadir frames is a stricter diagnostic than the
coverage union. It does not prove photogrammetric reconstruction or side overlap.
The yaw assumption matches the first experiment: camera follows each route leg.
"""

from __future__ import annotations

import math

from geoplan.routing.freeform import GeometryModel, image_footprint


def analyze(model: GeometryModel, flights: list[list[dict]]) -> dict:
    requested = model.case.request.parameters.forward_overlap_percent
    images = 0
    pairs = 0
    violations = 0
    turn_pairs = 0
    turn_violations = 0
    minimum = 100.0
    examples = []
    for flight in flights:
        clock = 0.0
        last_image = -math.inf
        previous = None
        previous_direction = None
        for leg in flight:
            duration = leg["minutes"]
            if leg["phase"] != "survey":
                previous = previous_direction = None
                clock += duration
                continue
            a, b = leg["start"], leg["end"]
            length = math.hypot(b[0] - a[0], b[1] - a[1])
            direction = ((b[0] - a[0]) / length, (b[1] - a[1]) / length)
            timestamp = max(clock, last_image + model.interval_minutes)
            while timestamp <= clock + duration + 1e-10:
                fraction = min(1, max(0, (timestamp - clock) / duration))
                center = (a[0] + fraction * (b[0] - a[0]), a[1] + fraction * (b[1] - a[1]))
                frame = image_footprint(center, a, b, model.width, model.height)
                images += 1
                if previous is not None and previous_direction is not None:
                    overlap = 100 * frame.intersection(previous).area / (model.width * model.height)
                    angle = abs(
                        direction[0] * previous_direction[1] - direction[1] * previous_direction[0]
                    )
                    turns = angle > 1e-6
                    below = overlap + 1e-7 < requested
                    pairs += 1
                    turn_pairs += turns
                    violations += below
                    turn_violations += turns and below
                    minimum = min(minimum, overlap)
                    if below and len(examples) < 5:
                        examples.append(
                            {
                                "overlap_percent": overlap,
                                "different_heading": turns,
                                "first_frame_local_m": list(previous.exterior.coords),
                                "second_frame_local_m": list(frame.exterior.coords),
                            }
                        )
                previous, previous_direction = frame, direction
                last_image = timestamp
                timestamp += model.interval_minutes
            clock += duration
    return {
        "images_reconstructed": images,
        "adjacent_camera_on_pairs": pairs,
        "requested_forward_overlap_percent": requested,
        "minimum_pair_intersection_percent": minimum,
        "pairs_below_requested_overlap": violations,
        "heading_change_pairs": turn_pairs,
        "heading_change_pairs_below_requested_overlap": turn_violations,
        "pair_overlap_passed": pairs > 0 and violations == 0,
        "examples": examples,
    }
