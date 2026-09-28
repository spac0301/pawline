"""Client for the codex-pets.net pet share API.

Deliberately stdlib-only: this runs from a CLI that must work without any
install step beyond Pillow.
"""

from __future__ import annotations

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any

from .errors import PetError

DEFAULT_API_BASE = "https://codex-pets.net"
SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED_FILES = ("pet.json", "spritesheet.webp")
TIMEOUT_SECONDS = 30


class RegistryError(PetError):
    """Raised when the share API cannot be used."""


def api_base() -> str:
    import os

    base = os.environ.get("CODEX_PETS_API_BASE") or DEFAULT_API_BASE
    return base.rstrip("/")


def normalize_slug(value: str, label: str = "pet id") -> str:
    slug = str(value or "").strip()
    if not SLUG_PATTERN.match(slug):
        raise RegistryError(f"{label} must be a lowercase slug like tiny-dino, got {value!r}")
    return slug


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "claude-pet"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            body = json.loads(exc.read().decode("utf-8"))
            detail = f": {body.get('error')}" if isinstance(body, dict) else ""
        except Exception:  # noqa: BLE001 - error body is best-effort
            pass
        raise RegistryError(f"{url} failed with HTTP {exc.code}{detail}") from exc
    except urllib.error.URLError as exc:
        raise RegistryError(f"{url} unreachable: {exc.reason}") from exc


def _request_json(url: str) -> dict[str, Any]:
    try:
        data = json.loads(_request(url).decode("utf-8"))
    except ValueError as exc:
        raise RegistryError(f"{url} returned invalid JSON") from exc
    if not isinstance(data, dict):
        raise RegistryError(f"{url} returned an unexpected payload")
    return data


def search(
    query: str = "",
    *,
    page: int = 1,
    page_size: int = 20,
    sort: str = "popular",
    kind: str = "",
    version: str = "",
) -> dict[str, Any]:
    """List packs from the gallery. `sort` is one of new/popular/likes/random."""
    params: list[tuple[str, str]] = [("page", str(page)), ("pageSize", str(page_size))]
    if sort:
        params.append(("sort", sort))
    if query.strip():
        params.append(("q", query.strip()))
    if kind and kind != "all":
        params.append(("kind", kind))
    if version in {"1", "2"}:
        params.append(("version", version))
    url = f"{api_base()}/api/pets?{urllib.parse.urlencode(params)}"
    payload = _request_json(url)
    return {"pets": payload.get("pets") or [], "total": payload.get("total") or 0}


def fetch_pet(pet_id: str) -> dict[str, Any]:
    slug = normalize_slug(pet_id)
    payload = _request_json(f"{api_base()}/api/pets/{slug}/share-data")
    pet = payload.get("pet")
    if not isinstance(pet, dict) or not pet.get("id"):
        raise RegistryError(f"{slug}: share API returned no pet")
    return pet


def fetch_collection(slug: str) -> dict[str, Any]:
    collection_slug = normalize_slug(slug, "collection slug")
    payload = _request_json(f"{api_base()}/api/collections/{collection_slug}")
    collection = payload.get("collection")
    pets = payload.get("pets")
    if not isinstance(collection, dict) or not isinstance(pets, list):
        raise RegistryError(f"{collection_slug}: collection lookup returned an unexpected payload")
    return {"collection": collection, "pets": pets}


def _download_zip(pet: dict[str, Any]) -> bytes:
    target = pet.get("downloadUrl") or f"/api/pets/{pet['id']}/download"
    url = f"{api_base()}{target}" if str(target).startswith("/") else str(target)
    return _request(url)


def install(pet_id: str, destination_root: Path) -> dict[str, Any]:
    """Download `pet_id` and write the pack into `destination_root/<id>/`."""
    pet = fetch_pet(pet_id)
    archive = _download_zip(pet)

    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise RegistryError(f"{pet['id']}: download was not a valid zip") from exc

    with bundle:
        names = {Path(name).name: name for name in bundle.namelist()}
        missing = [wanted for wanted in REQUIRED_FILES if wanted not in names]
        if missing:
            raise RegistryError(f"{pet['id']}: download is missing {', '.join(missing)}")

        manifest_bytes = bundle.read(names["pet.json"])
        try:
            manifest = json.loads(manifest_bytes.decode("utf-8"))
        except ValueError as exc:
            raise RegistryError(f"{pet['id']}: pet.json is not valid JSON") from exc
        if manifest.get("id") != pet["id"]:
            raise RegistryError(f"{pet['id']}: pet.json id does not match the requested pet")

        directory = destination_root / pet["id"]
        directory.mkdir(parents=True, exist_ok=True)
        for wanted in REQUIRED_FILES:
            (directory / wanted).write_bytes(bundle.read(names[wanted]))

    return {
        "id": pet["id"],
        "display_name": manifest.get("displayName") or pet["id"],
        "directory": directory,
    }


def install_local(source: str | Path, destination_root: Path) -> dict[str, Any]:
    """Install a pack from a local ``.codex-pet.zip`` (or plain ``.zip``) on disk.

    The gallery installer trusts the id it asked for; here there is no such id,
    so it is read from the archive's own ``pet.json`` -- the file name means
    nothing (``shoga-strings.codex-pet.zip`` carried an id of ``neon-gif-cat``).
    Only the two known members are ever read, by fixed name, so a crafted path
    inside the zip cannot escape the destination.
    """
    path = Path(source).expanduser()
    if not path.is_file():
        raise RegistryError(f"{path}: no such file")
    try:
        archive = path.read_bytes()
    except OSError as exc:
        raise RegistryError(f"{path}: could not be read ({exc})") from exc

    try:
        bundle = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise RegistryError(f"{path.name}: not a valid zip") from exc

    with bundle:
        names = {Path(name).name: name for name in bundle.namelist()}
        missing = [wanted for wanted in REQUIRED_FILES if wanted not in names]
        if missing:
            raise RegistryError(f"{path.name}: is missing {', '.join(missing)}")

        try:
            manifest = json.loads(bundle.read(names["pet.json"]).decode("utf-8"))
        except ValueError as exc:
            raise RegistryError(f"{path.name}: pet.json is not valid JSON") from exc
        try:
            pet_id = normalize_slug(manifest.get("id"))
        except RegistryError as exc:
            raise RegistryError(f"{path.name}: pet.json has no usable id ({exc})") from exc

        directory = destination_root / pet_id
        directory.mkdir(parents=True, exist_ok=True)
        for wanted in REQUIRED_FILES:
            (directory / wanted).write_bytes(bundle.read(names[wanted]))

    return {
        "id": pet_id,
        "display_name": manifest.get("displayName") or pet_id,
        "directory": directory,
    }


def install_collection(slug: str, destination_root: Path) -> list[dict[str, Any]]:
    collection = fetch_collection(slug)
    installed = []
    for pet in collection["pets"]:
        pet_id = pet.get("id")
        if not pet_id:
            continue
        installed.append(install(pet_id, destination_root))
    return installed
