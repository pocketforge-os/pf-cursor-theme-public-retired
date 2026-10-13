#!/usr/bin/env python3
"""Build PocketForge Xcursor themes from this fork's SVG sources.

The pipeline is deterministic and offline:

    shapes.toml -> two-tone restyled SVG -> PNG -> xcursorgen

Upstream shapes are read directly from ``theme/<variant>`` in this fork.
PocketForge's cursor-shape-v2 additions live in ``src/<variant>``.
"""
from __future__ import annotations

import argparse
import copy
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)
DESIGN_SIZE = 32
HOTSPOT_MARK = "#ff3dff"
UPSTREAM_COLOURS = {
    "light": {
        "#fbfbfb": "body", "white": "body", "#ffffff": "body",
        "black": "edge", "#000000": "edge",
    },
    "dark": {
        "#0d0d0d": "body", "black": "body", "#000000": "body",
        "white": "edge", "#ffffff": "edge", "#fbfbfb": "body",
    },
}
BADGE_COLOURS = {"#29cc52", "#3b78f1", "#7643e1", "#e24b4b", "#ffa200", "#f4e434"}
SPINNER_OPACITY = ["1", "0.86", "0.72", "0.58", "0.44", "0.3"]


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


class Shape:
    def __init__(self, name: str, spec: dict, variant: str):
        self.name = name
        self.source = spec["source"]
        self.aliases = list(spec.get("aliases", []))
        self.animate = spec.get("animate")
        self.hotspot = spec.get("hotspot")
        candidates = (
            HERE / "src" / variant / f"{self.source}_{DESIGN_SIZE}.svg",
            HERE / "theme" / variant / f"{self.source}_{DESIGN_SIZE}.svg",
        )
        for candidate in candidates:
            if candidate.is_file():
                self.path = candidate
                break
        else:
            raise FileNotFoundError(
                f"{name}: no fork source {self.source}_{DESIGN_SIZE}.svg for {variant}"
            )


def load_theme(path: Path) -> dict:
    with open(path, "rb") as stream:
        return tomllib.load(stream)


def read_hotspot(root: ET.Element) -> tuple[int, int]:
    for group in root.iter(q("g")):
        if group.get("id") == "hotspot":
            rect = group.find(q("rect"))
            x = float(rect.get("x", "0")) if rect is not None else 0.0
            y = float(rect.get("y", "0")) if rect is not None else 0.0
            return int(x), int(y)
    raise ValueError("no g#hotspot")


def strip_hotspot(root: ET.Element) -> None:
    for parent in root.iter():
        for child in list(parent):
            if child.tag == q("g") and child.get("id") == "hotspot":
                parent.remove(child)


def spinner_geometry(spinner: ET.Element) -> tuple[float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for path in spinner.iter(q("path")):
        numbers = [float(number) for number in re.findall(r"-?\d+(?:\.\d+)?", path.get("d", ""))]
        xs.extend(numbers[0::2])
        ys.extend(numbers[1::2])
    centre_x = (min(xs) + max(xs)) / 2
    centre_y = (min(ys) + max(ys)) / 2
    radius = max(max(xs) - min(xs), max(ys) - min(ys)) / 2
    return centre_x, centre_y, radius


def restyle(root: ET.Element, variant: str, fill: str, outline: str) -> None:
    vocabulary = UPSTREAM_COLOURS[variant]
    spinner = None
    in_spinner: set[int] = set()
    skip: set[int] = set()
    parents: dict[int, ET.Element] = {}
    order: list[ET.Element] = []
    for element in root.iter():
        if element.tag in (q("mask"), q("clipPath")):
            skip |= {id(child) for child in element.iter()}
        if element.tag == q("g") and element.get("id") == "spinner":
            spinner = element
            in_spinner = {id(child) for child in element.iter()}
        for child in element:
            parents[id(child)] = element
        if id(element) not in skip:
            order.append(element)

    def body_paint_after(element: ET.Element) -> bool:
        seen = False
        for later in order:
            if later is element:
                seen = True
                continue
            if seen and any(vocabulary.get(str(later.get(attr, "")).lower()) == "body"
                            for attr in ("fill", "stroke")):
                return True
        return False

    segment = 0
    for element in order:
        for attribute in ("fill", "stroke"):
            value = element.get(attribute)
            if value is None or value == "none":
                continue
            key = value.lower()
            if key == HOTSPOT_MARK:
                continue
            if key in BADGE_COLOURS:
                if id(element) in in_spinner:
                    element.set(attribute, fill)
                    element.set("fill-opacity", SPINNER_OPACITY[min(segment, len(SPINNER_OPACITY) - 1)])
                    segment += 1
                elif attribute == "fill" and not body_paint_after(element):
                    element.set(attribute, fill)
                    element.attrib.pop("fill-opacity", None)
                    edge = copy.deepcopy(element)
                    edge.attrib.pop("id", None)
                    edge.set("fill", "none")
                    edge.set("stroke", outline)
                    edge.set("stroke-width", "2")
                    edge.set("stroke-opacity", "0.65")
                    edge.set("stroke-linejoin", "round")
                    parent = parents[id(element)]
                    parent.insert(list(parent).index(element), edge)
                else:
                    element.set(attribute, outline)
                    element.attrib.pop("fill-opacity", None)
                continue
            role = vocabulary.get(key)
            if role is None:
                raise ValueError(f"unmapped colour {value!r} in {variant}")
            element.set(attribute, fill if role == "body" else outline)

    if spinner is not None:
        centre_x, centre_y, radius = spinner_geometry(spinner)
        ring = ET.Element(q("circle"), {
            "cx": f"{centre_x:.3f}", "cy": f"{centre_y:.3f}", "r": f"{radius:.3f}",
            "fill": "none", "stroke": outline, "stroke-opacity": "0.65", "stroke-width": "2",
        })
        spinner.insert(0, ring)


def rotated(root: ET.Element, angle: float) -> ET.Element:
    tree = copy.deepcopy(root)
    for group in tree.iter(q("g")):
        if group.get("id") == "spinner":
            centre_x, centre_y, _radius = spinner_geometry(group)
            previous = group.get("transform", "")
            group.set("transform", f"rotate({angle:.3f} {centre_x:.3f} {centre_y:.3f}) {previous}".strip())
            return tree
    raise ValueError("no g#spinner to animate")


def scaled_hotspot(x: int, y: int, size: int) -> tuple[int, int]:
    scale = size / DESIGN_SIZE
    return int(math.floor((x + 0.5) * scale)), int(math.floor((y + 0.5) * scale))


def render(svg_path: Path, png_path: Path, size: int) -> None:
    subprocess.run([
        "rsvg-convert", "-w", str(size), "-h", str(size), "-o", str(png_path), str(svg_path)
    ], check=True)


def build_variant(theme: dict, variant: str, out: Path, fill: str, outline: str,
                  theme_name: str, sizes: list[int], keep_svg: Path | None = None) -> dict:
    shapes = [Shape(name, spec, variant) for name, spec in theme["shapes"].items()]
    cursors = out / theme_name / "cursors"
    if cursors.exists():
        for path in cursors.iterdir():
            path.unlink()
    cursors.mkdir(parents=True, exist_ok=True)
    manifest = {
        "theme": theme_name, "variant": variant, "fill": fill, "outline": outline,
        "sizes": sizes, "shapes": {},
    }
    with tempfile.TemporaryDirectory(prefix="pf-cursor-build.", dir=os.environ.get("RUNNER_TEMP", "/tmp")) as tmp:
        temporary = Path(tmp)
        for shape in shapes:
            root = ET.parse(shape.path).getroot()
            hotspot_x, hotspot_y = read_hotspot(root)
            if shape.hotspot:
                hotspot_x, hotspot_y = shape.hotspot
            strip_hotspot(root)
            restyle(root, variant, fill, outline)
            if keep_svg is not None:
                keep_svg.mkdir(parents=True, exist_ok=True)
                (keep_svg / f"{shape.name}.svg").write_text(
                    ET.tostring(root, encoding="unicode") + "\n"
                )
            frame_count = shape.animate["frames"] if shape.animate else 1
            delay = shape.animate["ms"] if shape.animate else 0
            config_lines = []
            for size in sizes:
                xhot, yhot = scaled_hotspot(hotspot_x, hotspot_y, size)
                for frame in range(frame_count):
                    tree = rotated(root, 360.0 * frame / frame_count) if frame_count > 1 else root
                    svg_path = temporary / f"{shape.name}_{size}_{frame}.svg"
                    png_path = temporary / f"{shape.name}_{size}_{frame}.png"
                    svg_path.write_text(ET.tostring(tree, encoding="unicode"))
                    render(svg_path, png_path, size)
                    line = f"{size} {xhot} {yhot} {png_path.name}"
                    config_lines.append(line + (f" {delay}" if frame_count > 1 else ""))
            config = temporary / f"{shape.name}.cursor"
            config.write_text("\n".join(config_lines) + "\n")
            subprocess.run([
                "xcursorgen", "-p", str(temporary), str(config), str(cursors / shape.name)
            ], check=True)
            for alias in shape.aliases:
                link = cursors / alias
                if link.exists() or link.is_symlink():
                    link.unlink()
                link.symlink_to(shape.name)
            manifest["shapes"][shape.name] = {
                "hotspot": [hotspot_x, hotspot_y], "frames": frame_count,
                "aliases": shape.aliases, "source": str(shape.path.relative_to(HERE)),
            }

    theme_dir = out / theme_name
    (theme_dir / "index.theme").write_text(
        "[Icon Theme]\n"
        f"Name={theme_name}\n"
        f"Comment=PocketForge cursor theme ({variant}; fill {fill}, outline {outline}). "
        "Based on Phinger Cursors by Philipp Schaffrath, CC BY-SA 4.0.\n"
        "Inherits=\n"
    )
    (theme_dir / "cursor.theme").write_text(f"[Icon Theme]\nInherits={theme_name}\n")
    return manifest


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--shapes", type=Path, default=HERE / "shapes.toml")
    parser.add_argument("--out", type=Path, default=HERE / "build")
    parser.add_argument("--variant", choices=["light", "dark"], action="append")
    parser.add_argument("--fill", help="override body colour (#rrggbb)")
    parser.add_argument("--outline", help="override outline colour (#rrggbb)")
    parser.add_argument("--theme-name", help="one output theme name; requires exactly one --variant")
    parser.add_argument("--sizes", help="comma-separated sizes (default: shapes.toml)")
    parser.add_argument("--keep-svg", action="store_true")
    args = parser.parse_args(argv[1:])
    missing = [tool for tool in ("rsvg-convert", "xcursorgen") if shutil.which(tool) is None]
    if missing:
        print(f"required build tools missing: {', '.join(missing)}", file=sys.stderr)
        return 2
    theme = load_theme(args.shapes)
    sizes = [int(value) for value in args.sizes.split(",")] if args.sizes else list(theme["theme"]["sizes"])
    variants = args.variant or list(theme["theme"]["variants"])
    if args.theme_name and len(variants) != 1:
        print("--theme-name needs exactly one --variant", file=sys.stderr)
        return 2
    for variant in variants:
        colours = theme["theme"]["colours"][variant]
        fill = args.fill or colours["fill"]
        outline = args.outline or colours["outline"]
        name = args.theme_name or f"pocketforge-{variant}"
        manifest = build_variant(
            theme, variant, args.out, fill, outline, name, sizes,
            keep_svg=(args.out / "svg" / name) if args.keep_svg else None,
        )
        (args.out / name / "manifest.json").write_text(
            json.dumps(manifest, indent=1, sort_keys=True) + "\n"
        )
        print(f"built {name}: {len(manifest['shapes'])} shapes, sizes {sizes}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
