"""Animate one image into a complete pet pack.

This draws no art. It takes a picture you already have and derives the nine
animation rows the pet contract needs by transforming that one image: sine-driven
bob and lean, a squash-and-stretch jump arc, desaturation for failure, plus a
handful of marks drawn on top (`?`, a tick, a tear, sparkles).

So it is half of what Codex's `/hatch-pet` does. That generates the sprite art
itself with an image model; this only handles the packaging -- turning a subject
into nine rows at the right cell size. If you want generated art, generate the
image however you like and then point this at it.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance

from .errors import PetError
from .sprites import COLUMNS, ROW_STATES

CELL = (192, 208)

#: Frames per row, matching the published contract.
ROW_FRAMES = {
    "idle": 6,
    "running-right": 8,
    "running-left": 8,
    "waving": 4,
    "jumping": 5,
    "failed": 8,
    "waiting": 6,
    "running": 6,
    "review": 6,
}

#: How much of the cell the subject fills. Leaves room to bob and lean without
#: clipping at the edges.
FILL = 0.78

MARK = (255, 214, 102, 255)
SAD = (128, 208, 240, 255)


def _drop_background(image: Image.Image, tolerance: int = 26) -> Image.Image:
    """Make a flat background transparent, if the image has none already."""
    if image.getchannel("A").getextrema()[0] < 255:
        return image  # already has transparency; trust it

    width, height = image.size
    corners = [
        image.getpixel((0, 0)),
        image.getpixel((width - 1, 0)),
        image.getpixel((0, height - 1)),
        image.getpixel((width - 1, height - 1)),
    ]
    # Only treat it as a flat backdrop when the corners actually agree.
    reference = corners[0]
    for corner in corners[1:]:
        if sum(abs(a - b) for a, b in zip(corner[:3], reference[:3])) > tolerance * 3:
            return image

    pixels = image.load()
    for y in range(height):
        for x in range(width):
            red, green, blue, alpha = pixels[x, y]
            distance = sum(
                abs(channel - value) for channel, value in zip((red, green, blue), reference[:3])
            )
            if distance <= tolerance * 3:
                pixels[x, y] = (red, green, blue, 0)
    return image


def _prepare(source: Path) -> Image.Image:
    try:
        with Image.open(source) as opened:
            image = opened.convert("RGBA")
    except (OSError, ValueError) as exc:
        raise PetError(f"{source}: cannot read that image ({exc})") from exc

    image = _drop_background(image)
    box = image.getchannel("A").getbbox()
    if box is None:
        raise PetError(f"{source}: the image is fully transparent")
    image = image.crop(box)

    limit = (int(CELL[0] * FILL), int(CELL[1] * FILL))
    ratio = min(limit[0] / image.width, limit[1] / image.height)
    return image.resize(
        (max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
        Image.LANCZOS,
    )


def _place(
    subject: Image.Image,
    *,
    lift: int = 0,
    shift: int = 0,
    rotate: float = 0.0,
    squash: float = 0.0,
    fade: float = 0.0,
) -> Image.Image:
    """Compose the subject into one cell with the given deformation."""
    working = subject
    if squash:
        # Positive squashes wide and low, negative stretches tall and thin.
        width = max(1, round(subject.width * (1 + squash)))
        height = max(1, round(subject.height * (1 - squash)))
        working = subject.resize((width, height), Image.LANCZOS)
    if rotate:
        working = working.rotate(rotate, resample=Image.BICUBIC, expand=True)
    if fade:
        alpha = working.getchannel("A").point(lambda value: int(value * (1 - fade)))
        working = working.copy()
        working.putalpha(alpha)

    cell = Image.new("RGBA", CELL, (0, 0, 0, 0))
    left = (CELL[0] - working.width) // 2 + shift
    top = CELL[1] - working.height - lift
    cell.alpha_composite(working, (max(0, left), max(0, top)))
    return cell


def _question_mark(cell: Image.Image) -> None:
    canvas = ImageDraw.Draw(cell)
    x, y = int(CELL[0] * 0.78), int(CELL[1] * 0.12)
    width = max(3, CELL[0] // 40)
    canvas.arc([x - 14, y - 14, x + 14, y + 6], start=170, end=20, fill=MARK, width=width)
    canvas.line([(x + 6, y + 6), (x, y + 18)], fill=MARK, width=width)
    canvas.ellipse([x - 3, y + 26, x + 3, y + 32], fill=MARK)


def _tick(cell: Image.Image) -> None:
    canvas = ImageDraw.Draw(cell)
    x, y = int(CELL[0] * 0.16), int(CELL[1] * 0.18)
    width = max(4, CELL[0] // 32)
    canvas.line([(x - 10, y), (x, y + 12)], fill=MARK, width=width)
    canvas.line([(x, y + 12), (x + 18, y - 14)], fill=MARK, width=width)


def _tear(cell: Image.Image, progress: float) -> None:
    canvas = ImageDraw.Draw(cell)
    x = int(CELL[0] * 0.62)
    y = int(CELL[1] * (0.32 + progress * 0.3))
    radius = max(3, CELL[0] // 48)
    canvas.ellipse([x - radius, y - radius, x + radius, y + radius], fill=SAD)


def _sparkle(cell: Image.Image, index: int) -> None:
    canvas = ImageDraw.Draw(cell)
    for step in range(3):
        if (index + step) % 3:
            continue
        x = int(CELL[0] * (0.14 + 0.05 * step))
        y = int(CELL[1] * (0.24 + 0.07 * step))
        radius = max(2, CELL[0] // 60)
        canvas.ellipse([x - radius, y - radius, x + radius, y + radius], fill=MARK)


def _frames(name: str, subject: Image.Image, count: int) -> list[Image.Image]:
    frames: list[Image.Image] = []
    for index in range(count):
        phase = index / max(1, count) * math.tau
        progress = index / max(1, count - 1)

        if name == "idle":
            cell = _place(subject, lift=round(2 + 2 * math.sin(phase)))
        elif name in {"running-right", "running-left"}:
            cell = _place(
                subject,
                lift=round(3 + 4 * abs(math.sin(phase))),
                shift=round(5 * math.sin(phase)),
                rotate=-4 * math.sin(phase),
            )
            if name == "running-left":
                cell = cell.transpose(Image.FLIP_LEFT_RIGHT)
        elif name == "waving":
            cell = _place(subject, lift=2, rotate=7 * math.sin(progress * math.pi))
        elif name == "jumping":
            height = math.sin(progress * math.pi)
            cell = _place(
                subject,
                lift=round(2 + 26 * height),
                squash=0.12 if index in (0, count - 1) else -0.06 * height,
            )
        elif name == "failed":
            faded = ImageEnhance.Color(subject).enhance(0.35)
            cell = _place(faded, lift=0, squash=0.08, rotate=3 * math.sin(phase))
            if 0.2 < progress < 0.9:
                _tear(cell, progress)
        elif name == "waiting":
            cell = _place(subject, lift=round(2 + 2 * math.sin(phase)))
            if index % 4 < 2:
                _question_mark(cell)
        elif name == "running":
            cell = _place(subject, lift=2 + index % 2, rotate=2 * math.sin(phase))
            _sparkle(cell, index)
        elif name == "review":
            cell = _place(subject, lift=round(3 + 3 * math.sin(phase)))
            _tick(cell)
        else:  # pragma: no cover - ROW_STATES is closed
            cell = _place(subject)
        frames.append(cell)
    return frames


def hatch(
    source: str | Path,
    pet_id: str,
    display_name: str | None = None,
    destination_root: str | Path = ".",
) -> Path:
    """Build a v1 pack from `source` and write it under `destination_root`."""
    subject = _prepare(Path(source).expanduser())

    atlas = Image.new("RGBA", (CELL[0] * COLUMNS, CELL[1] * len(ROW_STATES)), (0, 0, 0, 0))
    for row, name in enumerate(ROW_STATES):
        for column, cell in enumerate(_frames(name, subject, ROW_FRAMES[name])):
            atlas.alpha_composite(cell, (column * CELL[0], row * CELL[1]))

    directory = Path(destination_root).expanduser() / pet_id
    directory.mkdir(parents=True, exist_ok=True)
    atlas.save(directory / "spritesheet.webp", lossless=True)
    manifest = {
        "id": pet_id,
        "displayName": display_name or pet_id,
        "description": f"Hatched from {Path(source).name} by claude-pet.",
        "spritesheetPath": "spritesheet.webp",
        "kind": "object",
    }
    (directory / "pet.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return directory
