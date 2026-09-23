#!/usr/bin/env python3
"""Render audited DXF modelspaces to PNG previews without altering the source."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import Frontend, RenderContext
from ezdxf.addons.drawing.config import BackgroundPolicy, ColorPolicy, Configuration
from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


FONT = Path("/Users/dmitrijmitin/Library/Fonts/GOST_A.TTF")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render one DXF to a PNG preview")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    document = ezdxf.readfile(args.source)
    audit = document.audit()
    if audit.has_errors:
        raise SystemExit("DXF audit failed: " + "; ".join(error.message for error in audit.errors))
    modelspace = document.modelspace()
    extents = bbox.extents(modelspace, fast=False)
    if not extents.has_data:
        raise SystemExit("DXF has no model-space geometry")

    # Preview only: use a Cyrillic-capable local font for the missing GOST/ESKD styles.
    if FONT.is_file():
        for style in document.styles:
            if style.dxf.name in {"GOST-2.304_Type-B", "GOST-2.304_Type-B_italic", "GOST-2.304_Type-B_shrink", "ЕСКД - 3", "GOST_A", "Текст 1", "Текст 1.5"}:
                style.dxf.font = str(FONT)

    width = extents.size.x
    height = extents.size.y
    margin = max(3.5, max(width, height) * 0.025)
    pixels_per_unit = min(12.0, max(4.0, 2400.0 / max(width, height)))
    image_width = max(480, int(math.ceil((width + 2 * margin) * pixels_per_unit)))
    image_height = max(480, int(math.ceil((height + 2 * margin) * pixels_per_unit)))
    dpi = 160
    figure = plt.figure(figsize=(image_width / dpi, image_height / dpi), dpi=dpi, facecolor="white", frameon=False)
    axis = figure.add_axes([0, 0, 1, 1], facecolor="white")
    axis.set_axis_off()
    axis.set_aspect("equal", adjustable="box")
    context = RenderContext(document)
    configuration = Configuration(background_policy=BackgroundPolicy.WHITE, color_policy=ColorPolicy.BLACK, min_lineweight=0.18)
    Frontend(context, MatplotlibBackend(axis, adjust_figure=False), config=configuration).draw_layout(modelspace, finalize=False)
    axis.set_xlim(extents.extmin.x - margin, extents.extmax.x + margin)
    axis.set_ylim(extents.extmin.y - margin, extents.extmax.y + margin)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(args.output, dpi=dpi, facecolor="white", edgecolor="none", transparent=False, pad_inches=0)
    plt.close(figure)
    print(f"Rendered {args.source.name}: {image_width}x{image_height}, entities={len(modelspace)}, audit=0")


if __name__ == "__main__":
    main()
