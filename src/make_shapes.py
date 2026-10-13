#!/usr/bin/env python3
"""Generate the two cursor-shape-v2 drawings upstream only aliases."""
from __future__ import annotations

import argparse
import copy
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
UPSTREAM = ROOT / "theme"
NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", NS)


def q(tag: str) -> str:
    return f"{{{NS}}}{tag}"


def load(variant: str, name: str) -> ET.Element:
    return ET.parse(UPSTREAM / variant / f"{name}_32.svg").getroot()


def by_id(root: ET.Element, element_id: str) -> ET.Element:
    for element in root.iter():
        if element.get("id") == element_id:
            return element
    raise KeyError(element_id)


def rename_ids(element: ET.Element, suffix: str) -> None:
    ids = {child.get("id") for child in element.iter() if child.get("id")}
    for child in element.iter():
        if child.get("id"):
            child.set("id", child.get("id") + suffix)
        for attribute, value in list(child.attrib.items()):
            match = re.fullmatch(r"url\(#([^)]+)\)", value)
            if match and match.group(1) in ids:
                child.set(attribute, f"url(#{match.group(1)}{suffix})")


def new_svg() -> ET.Element:
    return ET.Element(q("svg"), {
        "width": "32", "height": "32", "viewBox": "0 0 32 32", "fill": "none",
    })


def hotspot(x: int, y: int) -> ET.Element:
    group = ET.Element(q("g"), {"id": "hotspot"})
    ET.SubElement(group, q("rect"), {
        "id": "center", "opacity": "0.5", "x": str(x), "y": str(y),
        "width": "1", "height": "1", "fill": "#FF3DFF",
    })
    return group


def write(root: ET.Element, output: Path, variant: str, name: str) -> Path:
    path = output / variant / f"{name}_32.svg"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(ET.tostring(root, encoding="unicode") + "\n")
    return path


def dnd_ask(variant: str, output: Path) -> Path:
    hand_root = load(variant, "grabbing")
    help_root = load(variant, "help")
    root = new_svg()
    top = ET.SubElement(root, q("g"), {"id": f"cursor=dnd-ask, variant={variant}, size=32"})
    hand = copy.deepcopy(by_id(hand_root, "hand"))
    rename_ids(hand, "_hand")
    hand.set("transform", "translate(-4 -6)")
    top.append(hand)
    badge = copy.deepcopy(by_id(help_root, "badge"))
    rename_ids(badge, "_badge")
    top.append(badge)
    top.append(hotspot(12, 13))
    definitions = hand_root.find(q("defs"))
    if definitions is not None:
        definitions = copy.deepcopy(definitions)
        rename_ids(definitions, "_hand")
        root.append(definitions)
    return write(root, output, variant, "dnd-ask")


def all_resize(variant: str, output: Path) -> Path:
    first = load(variant, "nwse-resize")
    second = load(variant, "nesw-resize")
    root = new_svg()
    top = ET.SubElement(root, q("g"), {"id": f"cursor=all-resize, variant={variant}, size=32"})
    union = ET.SubElement(top, q("g"), {"id": "Union"})
    parts = []
    for source in (first, second):
        source_union = copy.deepcopy(by_id(source, "Union"))
        mask = source_union.find(q("mask"))
        paths = source_union.findall(q("path"))
        if mask is None or len(paths) != 2:
            raise RuntimeError("unexpected upstream structure for a resize arrow")
        parts.append((mask, paths[0], paths[1]))
    shared = ET.SubElement(union, q("mask"), {
        "id": "outside-both", "maskUnits": "userSpaceOnUse", "x": "0", "y": "0",
        "width": "32", "height": "32", "fill": "black",
    })
    ET.SubElement(shared, q("rect"), {
        "fill": "white", "x": "0", "y": "0", "width": "32", "height": "32",
    })
    for mask, _body, _outline in parts:
        shared.append(copy.deepcopy(mask.find(q("path"))))
    for _mask, body, _outline in parts:
        union.append(copy.deepcopy(body))
    for _mask, _body, outline in parts:
        outline = copy.deepcopy(outline)
        outline.set("mask", "url(#outside-both)")
        union.append(outline)
    top.append(hotspot(15, 15))
    return write(root, output, variant, "all-resize")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=HERE)
    args = parser.parse_args(argv[1:])
    for variant in ("light", "dark"):
        for generator in (dnd_ask, all_resize):
            print("wrote", generator(variant, args.out).relative_to(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
