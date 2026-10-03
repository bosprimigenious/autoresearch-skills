#!/usr/bin/env python3
"""Build deterministic, separately installable QA skill archives."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

try:
    from verify_skill_archive import validate_archive
except ModuleNotFoundError:  # Support importing as scripts.build_qa_release.
    from scripts.verify_skill_archive import validate_archive

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ("autoresearch-task-qa", "autoresearch-baseline-quality")
VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
RELEASE_REFERENCE = re.compile(r"autoresearch-qa-skills-([0-9]+\.[0-9]+\.[0-9]+)")
QA_VERSION_CONSTANT = re.compile(r'^(?:QA_)?SKILL_VERSION\s*=\s*["\']([^"\']+)["\']', re.MULTILINE)
EXCLUDED_NAMES = {"AGENTS.md", "CLAUDE.md", ".DS_Store"}
EXCLUDED_PARTS = {"__pycache__", ".pytest_cache"}
FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


def read_version() -> str:
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    if not VERSION_PATTERN.fullmatch(version):
        raise ValueError(f"VERSION must be plain SemVer without a prefix: {version!r}")
    return version


def validate_version_references(version: str) -> None:
    checked = [ROOT / "README.md"] + sorted(
        path
        for skill in ("autoresearch-task-qa", "autoresearch-task-authoring")
        for path in (ROOT / "skills" / skill).rglob("*")
        if path.is_file()
        and path.suffix in {".md", ".py", ".yaml", ".yml", ".json"}
        and path.name not in {"AGENTS.md", "CLAUDE.md"}
    )
    mismatches: list[str] = []
    found_release_reference = False
    for path in checked:
        text = path.read_text(encoding="utf-8")
        for referenced in RELEASE_REFERENCE.findall(text):
            found_release_reference = True
            if referenced != version:
                mismatches.append(f"{path.relative_to(ROOT)} references {referenced}")
        for embedded in QA_VERSION_CONSTANT.findall(text):
            if embedded != version:
                mismatches.append(f"{path.relative_to(ROOT)} embeds QA_SKILL_VERSION={embedded}")
    if not found_release_reference:
        mismatches.append("no autoresearch-qa-skills version reference found")
    if mismatches:
        raise ValueError("release version differs from VERSION: " + "; ".join(mismatches))


def distributable_files(skill_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in skill_dir.rglob("*"):
        relative = path.relative_to(skill_dir)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.name in EXCLUDED_NAMES or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError(f"release input contains a symlink: {path}")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda item: item.relative_to(skill_dir).as_posix())


def tree_sha256(skill_dir: Path, files: list[Path]) -> str:
    digest = hashlib.sha256()
    for path in files:
        relative = path.relative_to(skill_dir).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_archive(skill: str, version: str, out_dir: Path) -> dict[str, object]:
    skill_dir = ROOT / "skills" / skill
    files = distributable_files(skill_dir)
    if not files or skill_dir / "SKILL.md" not in files:
        raise ValueError(f"{skill}: missing distributable SKILL.md")

    archive = out_dir / f"{skill}-{version}.zip"
    temporary = archive.with_suffix(".zip.tmp")
    # Store without deflate so output bytes do not depend on a platform's zlib build.
    with ZipFile(temporary, "w", compression=ZIP_STORED) as bundle:
        for path in files:
            relative = path.relative_to(skill_dir).as_posix()
            info = ZipInfo(f"{skill}/{relative}", FIXED_ZIP_TIME)
            info.compress_type = ZIP_STORED
            info.create_system = 3
            info.external_attr = (0o100644 & 0xFFFF) << 16
            bundle.writestr(info, path.read_bytes(), compress_type=ZIP_STORED)
    temporary.replace(archive)

    errors = validate_archive(archive, skill)
    if errors:
        archive.unlink(missing_ok=True)
        raise ValueError(f"{skill}: invalid release archive: {'; '.join(errors)}")
    return {
        "file": archive.name,
        "sha256": file_sha256(archive),
        "bytes": archive.stat().st_size,
        "skill": skill,
        "skill_tree_sha256": tree_sha256(skill_dir, files),
    }


def build_release(out_dir: Path) -> list[Path]:
    version = read_version()
    validate_version_references(version)
    out_dir.mkdir(parents=True, exist_ok=True)
    expected_archives = {f"{skill}-{version}.zip" for skill in SKILLS}
    unexpected_archives = sorted(
        path.name for path in out_dir.glob("*.zip") if path.name not in expected_archives
    )
    if unexpected_archives:
        raise ValueError(
            "output directory contains unexpected ZIP files; use a clean release directory "
            f"and never ship a combined QA archive: {unexpected_archives!r}"
        )
    artifacts = [build_archive(skill, version, out_dir) for skill in SKILLS]
    manifest = {
        "schema_version": 1,
        "release": f"autoresearch-qa-skills-{version}",
        "version": version,
        "packaging_contract": "one-skill-per-zip",
        "artifacts": artifacts,
    }
    manifest_path = out_dir / f"autoresearch-qa-skills-{version}.manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    checksum_path = out_dir / f"autoresearch-qa-skills-{version}.sha256"
    checksum_entries = [
        (str(item["sha256"]), str(item["file"])) for item in artifacts
    ] + [(file_sha256(manifest_path), manifest_path.name)]
    checksum_path.write_text(
        "".join(f"{digest}  {name}\n" for digest, name in checksum_entries),
        encoding="utf-8",
    )
    return [out_dir / str(item["file"]) for item in artifacts] + [manifest_path, checksum_path]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    try:
        outputs = build_release(args.out_dir.resolve())
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
