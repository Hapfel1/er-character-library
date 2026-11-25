"""Copy a submission package into the repo with only known, size-limited content.

Usage: sanitize_submission.py <package_dir> <character_id>

metadata.json is rebuilt from an allowlist of fields with fixed types and
lengths. Screenshots are decoded and re-encoded as JPEG, which rejects files
that are not images and drops EXIF and other embedded metadata. The chosen
extensions are appended to $GITHUB_ENV for the following steps.
"""

import json
import os
import re
import sys
from pathlib import Path

from PIL import Image

Image.MAX_IMAGE_PIXELS = 40_000_000

MAX_METADATA_BYTES = 256 * 1024
MAX_IMAGE_BYTES = 15 * 1024 * 1024
MAX_EDGE = 3840
ID_RE = re.compile(r"^character_\d{3,}$")
STAT_KEYS = (
    "vigor",
    "mind",
    "endurance",
    "strength",
    "dexterity",
    "intelligence",
    "faith",
    "arcane",
)
INT_FIELDS = {
    "level": 1000,
    "max_hp": 100_000,
    "max_fp": 100_000,
    "max_stamina": 100_000,
    "runes": 2**31 - 1,
    "playtime_seconds": 2**31 - 1,
    "bosses_defeated": 10_000,
    "graces_unlocked": 10_000,
    "ng_plus": 100,
}
STR_FIELDS = {
    "name": 64,
    "class": 40,
    "body_type": 20,
    "playtime": 40,
    "ng_level": 20,
}
SLOT_RE = re.compile(r"^[a-z_]{1,20}\d?$")


def _int(value, limit: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return max(0, min(value, limit))


def _str(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = "".join(ch for ch in value if ch.isprintable())
    return cleaned[:limit]


def clean_metadata(raw: dict) -> dict:
    out: dict = {}
    for key, limit in STR_FIELDS.items():
        if key in raw:
            out[key] = _str(raw[key], limit)
    for key, limit in INT_FIELDS.items():
        if key in raw:
            out[key] = _int(raw[key], limit)
    stats = raw.get("stats")
    if isinstance(stats, dict):
        out["stats"] = {k: _int(stats.get(k), 99) for k in STAT_KEYS}
    equipment = raw.get("equipment")
    if isinstance(equipment, dict):
        eq = {}
        for slot, item in list(equipment.items())[:32]:
            if isinstance(slot, str) and SLOT_RE.match(slot) and isinstance(item, dict):
                eq[slot] = {
                    "id": _int(item.get("id"), 2**32 - 1),
                    "name": _str(item.get("name"), 80),
                }
        out["equipment"] = eq
    if "has_dlc" in raw:
        out["has_dlc"] = raw["has_dlc"] is True
    mod = raw.get("mod_info") or raw.get("overhaul_mod")
    if raw.get("requires_mod") is True and isinstance(mod, dict):
        info = {
            "name": _str(mod.get("name"), 60),
            "version": _str(mod.get("version"), 30),
        }
        out["requires_mod"] = True
        out["overhaul_mod"] = dict(info)
        out["mod_info"] = {**info, "required": True}
    return out


def reencode(src: Path, dest: Path) -> None:
    if src.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"{src.name} is larger than {MAX_IMAGE_BYTES} bytes")
    with Image.open(src) as probe:
        probe.verify()
    with Image.open(src) as img:
        img.load()
        img = img.convert("RGB")
        img.thumbnail((MAX_EDGE, MAX_EDGE))
        img.save(dest, "JPEG", quality=90, optimize=True)


def find_one(package: Path, stem: str) -> Path | None:
    for path in sorted(package.iterdir()):
        if path.is_file() and not path.is_symlink() and path.stem.lower() == stem:
            return path
    return None


def main() -> int:
    package, char_id = Path(sys.argv[1]), sys.argv[2]
    if not ID_RE.match(char_id):
        print(f"ERROR: unexpected character id {char_id!r}")
        return 1

    env_lines = []
    meta_src = find_one(package, "metadata")
    if meta_src and meta_src.suffix.lower() == ".json":
        if meta_src.stat().st_size > MAX_METADATA_BYTES:
            print("ERROR: metadata.json is too large")
            return 1
        raw = json.loads(meta_src.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            print("ERROR: metadata.json is not an object")
            return 1
        out = Path("metadata") / f"{char_id}.json"
        out.write_text(json.dumps(clean_metadata(raw), indent=2) + "\n", encoding="utf-8")
        print("Copied metadata (allowlisted fields)")
    else:
        print("No metadata.json found")

    for kind in ("face", "body", "preview"):
        src = find_one(package, kind)
        if src is None:
            continue
        dest = Path("screenshots") / kind / f"{char_id}.jpg"
        reencode(src, dest)
        env_lines.append(f"{kind}_ext=jpg")
        print(f"Copied {kind} screenshot (re-encoded JPEG)")

    env_file = os.environ.get("GITHUB_ENV")
    if env_file and env_lines:
        with open(env_file, "a", encoding="utf-8") as fh:
            fh.write("\n".join(env_lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
