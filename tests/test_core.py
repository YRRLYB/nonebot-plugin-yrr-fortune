from __future__ import annotations

import importlib.util
import string
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "nonebot_plugin_yrr_fortune"

_spec = importlib.util.spec_from_file_location("yrr_fortune_core", PACKAGE / "core.py")
core = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(core)

TEXTS = {
    "大凶": ["a"],
    "小凶": ["b"],
    "末吉": ["c"],
    "小吉": ["d"],
    "中吉": ["e"],
    "大吉": ["f"],
    "天命": ["g"],
}

SAMPLE_USER = {
    "qq_number": "00000000",
    "fortune": "大吉|★★★★★★☆",
    "fortune_date": "2026-11-16",
    "fortune_text": "测试签文",
    "radar_data": [{"axis": axis, "value": 0.8} for axis in core.RADAR_AXES],
    "lucky_items": {
        "color": "紫色",
        "number": 7,
        "direction": "东",
        "good": "搞钱、出行",
        "bad": "熬夜修仙、冲动消费",
    },
    "template_choice": "1",
}


class FortuneCoreTests(unittest.TestCase):
    def test_deterministic_per_day(self) -> None:
        first = core.generate_fortune("123456", "2026-11-16", TEXTS)
        second = core.generate_fortune("123456", "2026-11-16", TEXTS)
        self.assertEqual(first, second)
        self.assertEqual(first["fortune_date"], "2026-11-16")
        self.assertIn("★", first["fortune"])
        self.assertEqual(len(first["radar_data"]), 6)
        self.assertEqual(set(first["lucky_items"]), {"color", "number", "direction", "good", "bad"})

    def test_different_days_differ(self) -> None:
        first = core.generate_fortune("123456", "2026-11-16", TEXTS)
        second = core.generate_fortune("123456", "2026-11-17", TEXTS)
        self.assertNotEqual(first, second)

    def test_is_fresh(self) -> None:
        user = dict(SAMPLE_USER)
        self.assertTrue(core.is_fresh(user, "2026-11-16"))
        self.assertFalse(core.is_fresh(user, "2026-11-17"))
        broken = dict(user, radar_data=[])
        self.assertFalse(core.is_fresh(broken, "2026-11-16"))

    def test_lucky_activities_are_mutually_exclusive(self) -> None:
        groups = {}
        for side in ("good", "bad"):
            for item in core.ACTIVITY_DATA[side]:
                groups[item["name"]] = item["group"]

        import random

        for seed in range(300):
            rng = random.Random(seed)
            good, bad = core.get_lucky_activities(2, rng)
            good_names = good.split("、")
            bad_names = bad.split("、")
            self.assertEqual(len(good_names), 2)
            self.assertEqual(len(bad_names), 2)
            good_groups = {groups[name] for name in good_names}
            bad_groups = {groups[name] for name in bad_names}
            self.assertEqual(len(good_groups), 2)
            self.assertEqual(len(bad_groups), 2)
            self.assertFalse(good_groups & bad_groups)

    def test_templates_accept_the_context(self) -> None:
        context = core.build_render_context(
            user=SAMPLE_USER,
            level="大吉",
            stars="★★★★★★☆",
            background_image=core.background_data_uri(),
            avatar_url=core.avatar_data_uri("00000000"),
        )
        self.assertEqual(set(context), set(core.CONTEXT_KEYS))

        for index in range(1, 7):
            template = (PACKAGE / "templates" / f"{index}.html").read_text(encoding="utf-8")
            fields = set()
            for _, name, _, _ in string.Formatter().parse(template):
                if name:
                    fields.add(name.split(".")[0].split("[")[0])
            self.assertFalse(fields - set(context), f"template {index} has unknown fields")
            rendered = template.format(**context)
            self.assertIn("大吉", rendered)

    def test_radar_svg(self) -> None:
        svg = core.generate_radar_svg(SAMPLE_USER["radar_data"], "#a55eea")
        self.assertTrue(svg.startswith("<svg"))
        self.assertIn("财运", svg)
        self.assertIn("智慧", svg)
        self.assertEqual(core.generate_radar_svg([], "#000000"), "")

    def test_split_fortune(self) -> None:
        self.assertEqual(core.split_fortune("大吉|★★★★★★☆"), ("大吉", "★★★★★★☆"))
        self.assertEqual(core.split_fortune("坏数据")[0], "末吉")
        self.assertEqual(len(core.split_fortune("大吉|★")[1]), 7)

    def test_dedupe_texts(self) -> None:
        self.assertEqual(core.dedupe_texts({"大吉": ["x", "y", "x"]}), {"大吉": ["x", "y"]})

    def test_fallback_data_uris(self) -> None:
        self.assertTrue(core.background_data_uri().startswith("data:image/svg+xml;base64,"))
        self.assertTrue(core.avatar_data_uri("10001").startswith("data:image/svg+xml;base64,"))


if __name__ == "__main__":
    unittest.main()
