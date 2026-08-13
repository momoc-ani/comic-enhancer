from __future__ import annotations

import json


class MangaNinjaApiPoints:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "points_json": (
                    "STRING",
                    {"default": "[]", "multiline": False},
                )
            }
        }

    RETURN_TYPES = ("MINJIA_DATA",)
    RETURN_NAMES = ("xy_data",)
    FUNCTION = "parse"
    CATEGORY = "MangaNinjia"

    def parse(self, points_json: str):
        points = json.loads(points_json)
        if not isinstance(points, list) or len(points) > 16:
            raise ValueError("MangaNinja points must be a list with at most 16 items")
        normalized: list[list[int]] = []
        for point in points:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("MangaNinja point must contain x and y")
            x, y = (int(point[0]), int(point[1]))
            if not 0 <= x < 512 or not 0 <= y < 512:
                raise ValueError("MangaNinja point must be inside 512x512")
            normalized.append([x, y])
        return (normalized,)


NODE_CLASS_MAPPINGS = {"MangaNinjaApiPoints": MangaNinjaApiPoints}
NODE_DISPLAY_NAME_MAPPINGS = {"MangaNinjaApiPoints": "MangaNinja API Points"}
