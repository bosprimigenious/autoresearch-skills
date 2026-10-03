#!/usr/bin/env python3
"""Validate the checked-in skill-routing evaluation dataset."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "evals" / "qa-skill-routing.jsonl"
ALLOWED_CATEGORIES = {"direct", "indirect", "negative"}
ALLOWED_SKILLS = {"autoresearch-task-qa", "autoresearch-baseline-quality"}


def validate_dataset(path: Path) -> list[str]:
    errors: list[str] = []
    cases: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return [f"cannot read dataset: {exc}"]
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            errors.append(f"line {line_number}: blank lines are not allowed")
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"line {line_number}: invalid JSON: {exc.msg}")
            continue
        if not isinstance(value, dict):
            errors.append(f"line {line_number}: case must be an object")
            continue
        cases.append(value)

    if not 10 <= len(cases) <= 20:
        errors.append(f"dataset must contain 10-20 cases; found {len(cases)}")
    ids: set[str] = set()
    counts: Counter[str] = Counter()
    for index, case in enumerate(cases, 1):
        case_id = case.get("id")
        category = case.get("category")
        prompt = case.get("prompt")
        expected = case.get("expected_skills")
        prefix = f"case {index}"
        if not isinstance(case_id, str) or not case_id.strip():
            errors.append(f"{prefix}: id must be a non-empty string")
        elif case_id in ids:
            errors.append(f"{prefix}: duplicate id {case_id!r}")
        else:
            ids.add(case_id)
        if category not in ALLOWED_CATEGORIES:
            errors.append(f"{prefix}: invalid category {category!r}")
        else:
            counts[str(category)] += 1
        if not isinstance(prompt, str) or len(prompt.strip()) < 8:
            errors.append(f"{prefix}: prompt must contain at least 8 non-whitespace characters")
        if not isinstance(expected, list) or any(item not in ALLOWED_SKILLS for item in expected):
            errors.append(f"{prefix}: expected_skills must only contain known QA skills")
        elif len(expected) != len(set(expected)):
            errors.append(f"{prefix}: expected_skills contains duplicates")
        elif category == "negative" and expected:
            errors.append(f"{prefix}: negative cases must not route to a QA skill")
        elif category in {"direct", "indirect"} and not expected:
            errors.append(f"{prefix}: positive cases require at least one expected skill")
    for category in sorted(ALLOWED_CATEGORIES):
        if counts[category] < 3:
            errors.append(f"category {category!r} needs at least 3 cases; found {counts[category]}")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", nargs="?", type=Path, default=DEFAULT_DATASET)
    args = parser.parse_args()
    errors = validate_dataset(args.dataset)
    if errors:
        print("\n".join(f"ERROR: {error}" for error in errors), file=sys.stderr)
        return 1
    print(f"valid routing eval dataset: {args.dataset}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
