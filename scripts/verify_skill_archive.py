#!/usr/bin/env python3
"""Validate the structural contract for one distributable OpenAI skill ZIP."""

from __future__ import annotations

import argparse
import re
import stat
from pathlib import Path, PurePosixPath
from zipfile import BadZipFile, ZipFile

FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


def _field(frontmatter: str, name: str) -> str | None:
    match = re.search(rf"(?m)^{re.escape(name)}:\s*['\"]?([^'\"\n]+)['\"]?\s*$", frontmatter)
    return match.group(1).strip() if match else None


def validate_archive(archive: Path, expected_skill: str) -> list[str]:
    errors: list[str] = []
    try:
        with ZipFile(archive) as bundle:
            members = bundle.infolist()
            if not members:
                return ["archive is empty"]

            top_levels: set[str] = set()
            skill_files: list[str] = []
            seen: set[str] = set()
            for member in members:
                name = member.filename
                pure = PurePosixPath(name)
                if name in seen:
                    errors.append(f"duplicate member: {name}")
                seen.add(name)
                if "\\" in name or pure.is_absolute() or ".." in pure.parts:
                    errors.append(f"unsafe member path: {name}")
                    continue
                if not pure.parts:
                    errors.append("empty member path")
                    continue
                top_levels.add(pure.parts[0])
                mode = member.external_attr >> 16
                if stat.S_ISLNK(mode):
                    errors.append(f"symlink member is forbidden: {name}")
                if not member.is_dir() and pure.name == "SKILL.md":
                    skill_files.append(name)

            if top_levels != {expected_skill}:
                errors.append(
                    "archive must contain exactly one top-level skill directory "
                    f"{expected_skill!r}; found {sorted(top_levels)!r}"
                )
            expected_entry = f"{expected_skill}/SKILL.md"
            if skill_files != [expected_entry]:
                errors.append(
                    f"archive must contain exactly one SKILL.md at {expected_entry}; "
                    f"found {skill_files!r}"
                )
            elif expected_entry in seen:
                source = bundle.read(expected_entry).decode("utf-8", errors="replace")
                match = FRONTMATTER.match(source)
                if not match:
                    errors.append("SKILL.md has no valid YAML frontmatter boundary")
                elif _field(match.group(1), "name") != expected_skill:
                    errors.append("SKILL.md name does not match its top-level directory")
    except (BadZipFile, OSError) as exc:
        errors.append(f"cannot read ZIP: {exc}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--expected-skill", required=True)
    args = parser.parse_args()
    errors = validate_archive(args.archive, args.expected_skill)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1
    print(f"valid single-skill archive: {args.archive}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
