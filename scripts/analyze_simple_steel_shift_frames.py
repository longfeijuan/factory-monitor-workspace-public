#!/usr/bin/env python3
"""Build machine-light metrics and review sheets for the two simple-steel views."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


MACHINES = {
    "M1": (59, (1460, 650, 1740, 900)),
    "M2": (59, (1300, 470, 1430, 590)),
    "M3": (59, (1900, 525, 2130, 735)),
    "M4": (59, (1530, 390, 1740, 620)),
    "M5": (31, (1780, 660, 2030, 900)),
    "M6": (31, (1510, 330, 1760, 575)),
}

CAMERA_CROPS = {
    59: (1220, 320, 2190, 960),
    31: (1380, 250, 2130, 970),
}


def max_window_sum(mask: np.ndarray, size: int = 20) -> int:
    h, w = mask.shape
    if h < size or w < size:
        return int(mask.sum())
    table = np.pad(mask.astype(np.int32), ((1, 0), (1, 0))).cumsum(0).cumsum(1)
    windows = table[size:, size:] - table[:-size, size:] - table[size:, :-size] + table[:-size, :-size]
    return int(windows.max())


def metrics(image: Image.Image, box: tuple[int, int, int, int]) -> dict[str, float | int]:
    rgb = np.asarray(image.crop(box).convert("RGB"), dtype=np.int16)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    dominance = g - np.maximum(r, b)
    strict = (g >= 85) & (g - r >= 22) & (g - b >= 12)
    vivid = (g >= 130) & (g - r >= 35) & (g - b >= 20)
    return {
        "strict_pixels": int(strict.sum()),
        "vivid_pixels": int(vivid.sum()),
        "strict_window20": max_window_sum(strict, 20),
        "vivid_window20": max_window_sum(vivid, 20),
        "dominance_p999": float(np.percentile(dominance, 99.9)),
        "green_max": int(g.max()),
    }


def read_rows(frame_root: Path) -> list[dict[str, str]]:
    with (frame_root / "snapshots.csv").open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def label_font(size: int = 22):
    for name in ("/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            pass
    return ImageFont.load_default()


def build_contact(rows: list[dict[str, str]], channel: int, output: Path) -> None:
    crop_box = CAMERA_CROPS[channel]
    cell_w, cell_h = 320, 230
    cols = 12
    sheet = Image.new("RGB", (cols * cell_w, ((len(rows) + cols - 1) // cols) * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = label_font(20)
    for index, row in enumerate(rows):
        image = Image.open(row["image"]).convert("RGB").crop(crop_box)
        image.thumbnail((cell_w, cell_h - 30))
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x, y + 30))
        draw.text((x + 4, y + 3), row["start_local"][5:16].replace("T", " "), fill="black", font=font)
    output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(output, quality=90)


def build_full_contact(rows: list[dict[str, str]], output: Path, every: int = 6) -> None:
    selected = rows[::every]
    cell_w, cell_h = 480, 300
    cols = 6
    sheet = Image.new("RGB", (cols * cell_w, ((len(selected) + cols - 1) // cols) * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = label_font(22)
    for index, row in enumerate(selected):
        image = Image.open(row["image"]).convert("RGB")
        image.thumbnail((cell_w, cell_h - 35))
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x, y + 35))
        draw.text((x + 5, y + 4), row["start_local"][5:16].replace("T", " "), fill="black", font=font)
    sheet.save(output, quality=90)


def build_full_contact_chunks(rows: list[dict[str, str]], output_dir: Path, prefix: str) -> None:
    cell_w, cell_h, cols = 640, 395, 6
    font = label_font(24)
    for chunk_index in range(0, len(rows), 36):
        selected = rows[chunk_index:chunk_index + 36]
        sheet = Image.new("RGB", (cols * cell_w, 6 * cell_h), "white")
        draw = ImageDraw.Draw(sheet)
        for index, row in enumerate(selected):
            image = Image.open(row["image"]).convert("RGB")
            image.thumbnail((cell_w, cell_h - 38))
            x, y = (index % cols) * cell_w, (index // cols) * cell_h
            sheet.paste(image, (x, y + 38))
            draw.text((x + 5, y + 4), row["start_local"][5:16].replace("T", " "), fill="black", font=font)
        start_label = selected[0]["start_local"][11:16].replace(":", "")
        end_label = selected[-1]["start_local"][11:16].replace(":", "")
        sheet.save(output_dir / f"{prefix}-full-{start_label}-{end_label}.jpg", quality=91)


def build_machine_contact(rows: list[dict[str, str]], box: tuple[int, int, int, int], output: Path) -> None:
    cell_w, cell_h = 220, 170
    cols = 12
    sheet = Image.new("RGB", (cols * cell_w, ((len(rows) + cols - 1) // cols) * cell_h), "white")
    draw = ImageDraw.Draw(sheet)
    font = label_font(17)
    for index, row in enumerate(rows):
        image = Image.open(row["image"]).convert("RGB").crop(box)
        image.thumbnail((cell_w, cell_h - 28))
        x, y = (index % cols) * cell_w, (index // cols) * cell_h
        sheet.paste(image, (x, y + 28))
        draw.text((x + 3, y + 2), row["start_local"][5:16].replace("T", " "), fill="black", font=font)
    sheet.save(output, quality=92)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    roots = {59: args.root / "ch59-frames", 31: args.root / "ch31-frames"}
    rows_by_channel = {channel: read_rows(root) for channel, root in roots.items()}

    output_rows = []
    for machine, (channel, box) in MACHINES.items():
        for row in rows_by_channel[channel]:
            image = Image.open(row["image"]).convert("RGB")
            output_rows.append({
                "machine": machine,
                "channel": channel,
                "start_local": row["start_local"],
                "image": row["image"],
                **metrics(image, box),
            })
    with (args.root / "green-metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0]))
        writer.writeheader()
        writer.writerows(output_rows)

    for channel, rows in rows_by_channel.items():
        build_contact(rows, channel, args.root / f"ch{channel}-lights-5min.jpg")
        build_full_contact(rows, args.root / f"ch{channel}-full-30min.jpg")
        build_full_contact_chunks(rows, args.root, f"ch{channel}")
    for machine, (channel, box) in MACHINES.items():
        build_machine_contact(rows_by_channel[channel], box, args.root / f"{machine.lower()}-light-5min.jpg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
