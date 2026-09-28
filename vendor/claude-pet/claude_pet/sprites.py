"""Loader for Codex-pet-compatible sprite packs.

A pet pack is a directory holding `pet.json` plus the spritesheet it names
(always `spritesheet.webp` for packs published by codex-pets.net).

Two atlas layouts exist in the wild and both are supported:

    v1   1536x1872   8 columns x  9 rows   cells 192x208
    v2   1536x2288   8 columns x 11 rows   cells 192x208

Rows 0-8 carry the animation states listed in `ROW_STATES`. v2 adds rows 9-10,
which hold 16 static "look direction" poses rather than an animation.

The per-row frame count is *measured* from the alpha channel instead of being
taken from a table: the published contract gives nominal counts, but real packs
sometimes draw an extra frame, and trailing cells are required to be fully
transparent. Measuring keeps us compatible with both.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

from .errors import PetError

COLUMNS = 8
CELL_ASPECT = 192 / 208

#: Row index -> animation name, per the Codex pet contract.
ROW_STATES: tuple[str, ...] = (
    "idle",
    "running-right",
    "running-left",
    "waving",
    "jumping",
    "failed",
    "waiting",
    "running",
    "review",
)

#: Rows that hold look-direction poses instead of an animation (v2 only).
LOOK_ROWS: tuple[int, ...] = (9, 10)

_ROWS_BY_VERSION = {1: 9, 2: 11}


class SpriteError(PetError):
    """Raised when a pet pack cannot be interpreted."""


@dataclass
class Pet:
    """A loaded sprite pack, ready to animate."""

    id: str
    display_name: str
    description: str
    directory: Path
    version: int
    kind: str
    cell: tuple[int, int]
    animations: dict[str, list[Image.Image]] = field(repr=False, default_factory=dict)
    looks: list[Image.Image] = field(repr=False, default_factory=list)

    @property
    def frame_counts(self) -> dict[str, int]:
        return {name: len(frames) for name, frames in self.animations.items()}

    def frames(self, state: str) -> list[Image.Image]:
        """Frames for `state`, falling back to idle then to any populated row."""
        for candidate in (state, "idle", "waiting", "running"):
            frames = self.animations.get(candidate)
            if frames:
                return frames
        for frames in self.animations.values():
            if frames:
                return frames
        raise SpriteError(f"{self.id}: spritesheet has no visible frames")


def resolve_layout(size: tuple[int, int]) -> tuple[int, int, tuple[int, int]]:
    """Return (version, rows, cell) for an atlas of `size`.

    Accepts the two canonical atlases plus proportionally scaled variants, so a
    pack drawn at half resolution still loads.
    """
    width, height = size
    if width <= 0 or height <= 0:
        raise SpriteError("spritesheet has zero width or height")
    if width % COLUMNS:
        raise SpriteError(f"spritesheet width {width} is not divisible by {COLUMNS} columns")

    cell_width = width // COLUMNS
    best: tuple[float, int, int] | None = None
    for version, rows in _ROWS_BY_VERSION.items():
        if height % rows:
            continue
        cell_height = height // rows
        error = abs((cell_width / cell_height) - CELL_ASPECT)
        if best is None or error < best[0]:
            best = (error, version, rows)

    if best is None:
        raise SpriteError(
            f"spritesheet {width}x{height} matches neither the v1 (8x9) nor v2 (8x11) grid"
        )

    _, version, rows = best
    return version, rows, (cell_width, height // rows)


def _row_frames(sheet: Image.Image, row: int, cell: tuple[int, int]) -> list[Image.Image]:
    """Cells of `row` up to the last one that has any opaque pixel."""
    cell_width, cell_height = cell
    top = row * cell_height
    frames: list[Image.Image] = []
    for column in range(COLUMNS):
        left = column * cell_width
        tile = sheet.crop((left, top, left + cell_width, top + cell_height))
        frames.append(tile)

    last = -1
    for index, tile in enumerate(frames):
        if tile.getchannel("A").getbbox() is not None:
            last = index
    return frames[: last + 1]


def load_pet(directory: str | Path) -> Pet:
    """Load the pet pack in `directory`."""
    directory = Path(directory).expanduser()
    manifest_path = directory / "pet.json"
    if not manifest_path.is_file():
        raise SpriteError(f"{directory}: no pet.json")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SpriteError(f"{manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SpriteError(f"{manifest_path}: expected a JSON object")

    sheet_name = manifest.get("spritesheetPath") or "spritesheet.webp"
    sheet_path = directory / sheet_name
    if not sheet_path.is_file():
        raise SpriteError(f"{directory}: missing spritesheet {sheet_name!r}")

    try:
        with Image.open(sheet_path) as opened:
            sheet = opened.convert("RGBA")
    except OSError as exc:
        raise SpriteError(f"{sheet_path}: {exc}") from exc

    # Atlas geometry decides the layout; `spriteVersionNumber` is advisory,
    # since the renderer has to match the pixels it actually got.
    version, rows, cell = resolve_layout(sheet.size)

    animations: dict[str, list[Image.Image]] = {}
    for row in range(min(rows, len(ROW_STATES))):
        animations[ROW_STATES[row]] = _row_frames(sheet, row, cell)

    looks: list[Image.Image] = []
    for row in LOOK_ROWS:
        if row < rows:
            looks.extend(_row_frames(sheet, row, cell))

    pet_id = str(manifest.get("id") or directory.name)
    return Pet(
        id=pet_id,
        display_name=str(manifest.get("displayName") or pet_id),
        description=str(manifest.get("description") or ""),
        directory=directory,
        version=version,
        kind=str(manifest.get("kind") or "object"),
        cell=cell,
        animations=animations,
        looks=looks,
    )


def scale_frames(frames: list[Image.Image], height: int) -> list[Image.Image]:
    """Resize `frames` to `height` pixels tall, preserving aspect ratio."""
    if not frames:
        return []
    source_height = frames[0].height
    if source_height == height:
        return list(frames)
    ratio = height / source_height
    width = max(1, round(frames[0].width * ratio))
    return [frame.resize((width, height), Image.LANCZOS) for frame in frames]


def contact_sheet(pet: Pet, scale: float = 0.45) -> Image.Image:
    """Render every animation row into one image, for eyeballing a pack."""
    cell_width, cell_height = pet.cell
    tile_width = max(1, round(cell_width * scale))
    tile_height = max(1, round(cell_height * scale))

    rows = [(name, pet.animations[name]) for name in ROW_STATES if name in pet.animations]
    if pet.looks:
        rows.append(("look-directions", pet.looks[:COLUMNS]))
        rows.append(("look-directions", pet.looks[COLUMNS:]))

    canvas = Image.new(
        "RGBA", (tile_width * COLUMNS, tile_height * len(rows)), (24, 24, 32, 255)
    )
    for index, (_, frames) in enumerate(rows):
        for column, frame in enumerate(frames[:COLUMNS]):
            tile = frame.resize((tile_width, tile_height), Image.LANCZOS)
            canvas.alpha_composite(tile, (column * tile_width, index * tile_height))
    return canvas
