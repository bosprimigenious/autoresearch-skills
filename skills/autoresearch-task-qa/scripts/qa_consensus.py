#!/usr/bin/env python3
"""Fail-closed consensus for exactly two independent release QA reports."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

SKILL_NAME = "autoresearch-qa-skills"
SKILL_VERSION = "0.3.4"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verdicts(report: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for section, rows in (
        ("qa", report.get("checks", [])),
        ("gate", report.get("content_gates", {}).get("checks", [])),
        ("harbor", report.get("harbor", {}).get("checks", [])),
    ):
        if isinstance(rows, list):
            for row in rows:
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    result[f"{section}:{row['id']}"] = row.get("status")
    return result


def aggregate(paths: list[Path]) -> tuple[dict, bool]:
    errors: list[str] = []
    reports: list[dict] = []
    report_refs: list[dict] = []
    for path in paths:
        resolved = path.expanduser().resolve()
        try:
            report = json.loads(resolved.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            errors.append(f"{resolved.name}: cannot read report.json ({type(exc).__name__})")
            report = {}
        if not isinstance(report, dict):
            errors.append(f"{resolved.name}: report must be a JSON object")
            report = {}
        reports.append(report)
        report_refs.append({"path": resolved.name, "sha256": sha256_file(resolved) if resolved.is_file() else None})

    artifacts = [report.get("source", {}).get("sha256") for report in reports]
    for index, report in enumerate(reports, 1):
        label = f"report {index}"
        source = report.get("source", {})
        qa_run = report.get("qa_run", {})
        skill = qa_run.get("skill", {})
        reviewer = qa_run.get("reviewer", {})
        if report.get("summary", {}).get("decision") != "PASS":
            errors.append(label + " decision is not PASS")
        if source.get("kind") != "zip" or not isinstance(source.get("sha256"), str) or len(source["sha256"]) != 64:
            errors.append(label + " is not bound to a ZIP SHA256")
        if qa_run.get("artifact_sha256") != source.get("sha256") or qa_run.get("input_kind") != "zip":
            errors.append(label + " qa_run provenance does not match source")
        if skill != {"name": SKILL_NAME, "version": SKILL_VERSION}:
            errors.append(label + f" must use {SKILL_NAME}/{SKILL_VERSION}")
        if qa_run.get("clean_context") is not True:
            errors.append(label + " lacks clean-context attestation")
        for field in ("provider", "model", "version", "session_id"):
            if not isinstance(reviewer.get(field), str) or not reviewer[field].strip():
                errors.append(label + f" reviewer.{field} is missing")
    if len(set(artifacts)) != 1 or not artifacts[0]:
        errors.append("reports do not reference the same artifact SHA256")

    reviewers = [report.get("qa_run", {}).get("reviewer", {}) for report in reports]
    identities = [(row.get("provider"), row.get("model"), row.get("version")) for row in reviewers]
    if len(set(identities)) != 2:
        errors.append("reviewers must use two different provider/model/version identities")
    sessions = [row.get("session_id") for row in reviewers]
    if len(set(sessions)) != 2 or not all(sessions):
        errors.append("reviewers must use two different non-empty session IDs")

    left, right = (verdicts(report) for report in reports)
    disagreements = []
    for key in sorted(set(left) | set(right)):
        if left.get(key) != right.get(key):
            disagreements.append({"check": key, "report_1": left.get(key), "report_2": right.get(key)})

    output = {
        "schema_version": 1,
        "skill": {"name": SKILL_NAME, "version": SKILL_VERSION},
        "status": "PASS" if not errors and not disagreements else "NOT_READY",
        "artifact_sha256": artifacts[0] if artifacts and len(set(artifacts)) == 1 else None,
        "qa_reports": report_refs,
        "report_sha256s": [row["sha256"] for row in report_refs],
        "reviewers": reviewers,
        "unresolved_disagreements": disagreements,
        "validation_errors": errors,
    }
    return output, output["status"] == "PASS"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reports", nargs=2, type=Path, metavar="REPORT_JSON")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    out = args.out.expanduser().resolve()
    if out.exists():
        parser.error("consensus output already exists; do not overwrite release evidence")
    result, passed = aggregate(args.reports)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "consensus": str(out)}, ensure_ascii=False))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
