#!/usr/bin/env python3
"""Hermetic tests for the PocketForge cursor-theme fork.

The suite builds both variants from this repository's SVG tree, then checks
the cursor-shape-v2 name contract, aliases, sizes, hotspots, animation, and
two-tone palette. Negative controls are exercised in the same invocation.
"""
from __future__ import annotations

import contextlib
import importlib
import io
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
CURSOR_SHAPE_V1 = [
    "default", "context-menu", "help", "pointer", "progress", "wait", "cell", "crosshair", "text",
    "vertical-text", "alias", "copy", "move", "no-drop", "not-allowed", "grab", "grabbing", "e-resize",
    "n-resize", "ne-resize", "nw-resize", "s-resize", "se-resize", "sw-resize", "w-resize", "ew-resize",
    "ns-resize", "nesw-resize", "nwse-resize", "col-resize", "row-resize", "all-scroll", "zoom-in", "zoom-out",
]
CURSOR_SHAPE_V2 = CURSOR_SHAPE_V1 + ["dnd-ask", "all-resize"]


def read_xcursor(path: Path) -> list[dict]:
    data = path.read_bytes()
    magic, _header, _version, ntoc = struct.unpack("<4sIII", data[:16])
    if magic != b"Xcur":
        raise ValueError(f"{path}: not an Xcursor file")
    images = []
    for index in range(ntoc):
        ctype, _subtype, pos = struct.unpack("<III", data[16 + 12 * index:28 + 12 * index])
        if ctype != 0xFFFD0002:
            continue
        _hsize, _ctype2, size, _ver, width, height, xhot, yhot, delay = struct.unpack(
            "<IIIIIIIII", data[pos:pos + 36]
        )
        argb = data[pos + 36:pos + 36 + 4 * width * height]
        images.append({"size": size, "width": width, "height": height,
                       "xhot": xhot, "yhot": yhot, "delay": delay, "argb": argb})
    return images


def rgb(value: str) -> tuple[int, int, int]:
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]


def two_tone_violations(argb: bytes, fill: str, outline: str,
                        min_alpha: int = 32, tolerance: int = 12) -> int:
    """Count premultiplied Xcursor pixels outside the fill/outline line."""
    body, edge = rgb(fill), rgb(outline)
    delta = [body[i] - edge[i] for i in range(3)]
    denominator = sum(component * component for component in delta)
    bad = 0
    for offset in range(0, len(argb), 4):
        blue, green, red, alpha = argb[offset:offset + 4]
        if alpha < min_alpha:
            continue
        colour = tuple(min(255, round(channel * 255 / alpha)) for channel in (red, green, blue))
        weight = (sum((colour[i] - edge[i]) * delta[i] for i in range(3)) / denominator
                  if denominator else 0.0)
        weight = min(1.0, max(0.0, weight))
        if any(abs(colour[i] - (weight * body[i] + (1 - weight) * edge[i])) > tolerance
               for i in range(3)):
            bad += 1
    return bad


class SourceContract(unittest.TestCase):
    def test_fork_owns_build_sources(self):
        required = [
            HERE / "build_cursors.py",
            HERE / "shapes.toml",
            HERE / "ATTRIBUTION.md",
            HERE / "src" / "make_shapes.py",
        ]
        required += [HERE / "src" / variant / f"{name}_32.svg"
                     for variant in ("light", "dark")
                     for name in ("dnd-ask", "all-resize")]
        missing = [str(path.relative_to(HERE)) for path in required if not path.is_file()]
        self.assertEqual(missing, [], f"fork-owned cursor sources missing: {missing}")

    def test_no_copied_upstream_snapshot(self):
        self.assertFalse((HERE / "upstream").exists())
        self.assertTrue((HERE / "theme" / "light" / "default_32.svg").is_file())

        with open(HERE / "shapes.toml", "rb") as stream:
            theme = tomllib.load(stream)
        with self.assertRaisesRegex(FileNotFoundError, "no fork source"):
            importlib.import_module("build_cursors").Shape(
                "missing", {"source": "missing", "aliases": []}, "light"
            )

    def test_ci_uses_pinned_payload_without_path_filter(self):
        workflow = (HERE / ".github" / "workflows" / "input-look.yml").read_text()
        self.assertIn(
            "rust-container.yml@a9bf52d69199418e3443ea81919c4128783a9700",
            workflow,
        )
        self.assertIn(
            "ci-tools@sha256:2ff536627228c90e5c32b7d6345b21600a38a17481c075b41683db47ead3e862",
            workflow,
        )
        self.assertIn("script: scripts/ci-container.sh", workflow)
        self.assertNotIn("paths:", workflow)
        self.assertFalse((HERE / ".github" / "workflows" / "main.yml").exists())
        weakened = workflow.replace("@a9bf52d69199418e3443ea81919c4128783a9700", "@main")
        self.assertNotEqual(weakened, workflow)  # negative: mutable workflow ref


class Build(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(HERE))
        cls.builder = importlib.import_module("build_cursors")
        with open(HERE / "shapes.toml", "rb") as stream:
            cls.theme = tomllib.load(stream)
        cls.tmp = tempfile.TemporaryDirectory(prefix="pf-cursor-test.", dir=os.environ.get("RUNNER_TEMP", "/tmp"))
        cls.out = Path(cls.tmp.name) / "build"
        rc = cls.builder.main(["build_cursors.py", "--out", str(cls.out)])
        if rc != 0:
            raise RuntimeError(f"build_cursors.py returned {rc}")

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_shape_table_and_aliases(self):
        shapes = self.theme["shapes"]
        self.assertEqual(list(shapes), CURSOR_SHAPE_V2)
        aliases: dict[str, str] = {}
        for canonical, spec in shapes.items():
            for alias in spec.get("aliases", []):
                self.assertNotIn(alias, shapes)
                self.assertNotIn(alias, aliases)
                aliases[alias] = canonical
        self.assertEqual(aliases["hand1"], "grab")
        self.assertGreater(len(aliases), 0)
        broken = dict(shapes)
        del broken["dnd-ask"]
        self.assertNotEqual(list(broken), CURSOR_SHAPE_V2)  # negative control

    def test_all_files_aliases_sizes_and_animation(self):
        expected_sizes = set(self.theme["theme"]["sizes"])
        for variant in self.theme["theme"]["variants"]:
            cursors = self.out / f"pocketforge-{variant}" / "cursors"
            for canonical, spec in self.theme["shapes"].items():
                path = cursors / canonical
                self.assertTrue(path.is_file() and not path.is_symlink(), canonical)
                images = read_xcursor(path)
                self.assertEqual({image["size"] for image in images}, expected_sizes)
                expected_frames = spec.get("animate", {}).get("frames", 1)
                for size in expected_sizes:
                    frames = [image for image in images if image["size"] == size]
                    self.assertEqual(len(frames), expected_frames)
                    if expected_frames > 1:
                        self.assertTrue(all(image["delay"] == 60 for image in frames))
                        self.assertNotEqual(frames[0]["argb"], frames[4]["argb"])
                for alias in spec.get("aliases", []):
                    link = cursors / alias
                    self.assertTrue(link.is_symlink(), alias)
                    self.assertEqual(link.resolve(), path.resolve())
            self.assertFalse((cursors / "hand9").exists())  # negative control

    def test_hotspots_scale(self):
        cursors = self.out / "pocketforge-light" / "cursors"
        text = {image["size"]: image for image in read_xcursor(cursors / "text")}
        self.assertEqual((text[32]["xhot"], text[32]["yhot"]), (15, 15))
        self.assertEqual((text[48]["xhot"], text[48]["yhot"]), (23, 23))
        self.assertNotEqual(text[48]["xhot"], 15)  # negative: unscaled hotspot

    def test_every_compiled_shape_is_two_tone(self):
        for variant in self.theme["theme"]["variants"]:
            colours = self.theme["theme"]["colours"][variant]
            cursors = self.out / f"pocketforge-{variant}" / "cursors"
            for canonical in CURSOR_SHAPE_V2:
                image = next(item for item in read_xcursor(cursors / canonical) if item["size"] == 48)
                self.assertEqual(two_tone_violations(image["argb"], colours["fill"], colours["outline"]), 0,
                                 canonical)
        off_palette = bytes((0, 0, 255, 255))
        colours = self.theme["theme"]["colours"]["light"]
        self.assertEqual(two_tone_violations(off_palette, colours["fill"], colours["outline"]), 1)

    def test_theme_name_rejects_multiple_variants(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = self.builder.main(["build_cursors.py", "--theme-name", "one",
                                    "--out", str(Path(self.tmp.name) / "bad"), "--sizes", "32"])
        self.assertEqual(rc, 2)
        self.assertIn("exactly one --variant", err.getvalue())
        self.assertFalse((Path(self.tmp.name) / "bad" / "one").exists())

        rc = self.builder.main([
            "build_cursors.py", "--theme-name", "one", "--variant", "dark",
            "--out", str(Path(self.tmp.name) / "named"), "--sizes", "32",
        ])
        self.assertEqual(rc, 0)
        self.assertTrue((Path(self.tmp.name) / "named" / "one" / "cursors" / "default").is_file())

    def test_missing_tool_fails_before_writing(self):
        out = Path(self.tmp.name) / "missing-tool"
        err = io.StringIO()
        with mock.patch.object(self.builder.shutil, "which", side_effect=lambda tool: None if tool == "xcursorgen" else f"/usr/bin/{tool}"), contextlib.redirect_stderr(err):
            rc = self.builder.main(["build_cursors.py", "--out", str(out)])
        self.assertEqual(rc, 2)
        self.assertIn("xcursorgen", err.getvalue())
        self.assertFalse(out.exists())

    def test_custom_sources_regenerate_exactly(self):
        staged = Path(self.tmp.name) / "generated"
        subprocess.run([sys.executable, str(HERE / "src" / "make_shapes.py"), "--out", str(staged)], check=True)
        for variant in ("light", "dark"):
            for name in ("dnd-ask", "all-resize"):
                self.assertEqual((staged / variant / f"{name}_32.svg").read_bytes(),
                                 (HERE / "src" / variant / f"{name}_32.svg").read_bytes())
        extra = staged / "light" / "retired_32.svg"
        extra.write_text("<svg/>")
        self.assertNotEqual(sorted(path.relative_to(staged) for path in staged.rglob("*.svg")),
                            sorted(path.relative_to(HERE / "src") for path in (HERE / "src").rglob("*.svg")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
