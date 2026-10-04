#!/usr/bin/env python3
"""Fail closed when an AutoResearch authoring milestone lacks evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


PLACEHOLDERS = {"", "todo", "tbd", "unknown", "none", "n/a"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
QA_SKILL_NAME = "autoresearch-qa-skills"
QA_SKILL_VERSION = "0.3.4"
STAGES = {
    "selection": (
        "source_identity", "license_evidence", "optimization_surface", "duplicate_registry_evidence",
    ),
    "pilot": (
        "baseline_receipt", "reference_receipt", "effect_noise_decision", "evaluator_recompute",
    ),
    "container": (
        "agent_image_digest", "verifier_image_digest", "hidden_isolation_probe", "target_harness_receipt",
    ),
    "long_run": ("track_a_lineage", "track_b_lineage", "snapshot_restore_probe"),
    "release": ("qa_reports", "qa_consensus", "attachment_privacy_report", "artifact_manifest"),
}


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in PLACEHOLDERS)


def _string(value: object) -> str | None:
    if not isinstance(value, str) or _missing(value):
        return None
    return value.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _reference(
    value: object, *, field: str, base_dir: Path, issues: list[str]
) -> tuple[Path, str, dict[str, Any]] | None:
    if not isinstance(value, dict):
        issues.append(f"{field}: expected object with path and sha256")
        return None
    raw_path = _string(value.get("path"))
    expected = _string(value.get("sha256"))
    if raw_path is None:
        issues.append(f"{field}.path: missing")
        return None
    if expected is None or not SHA256_RE.fullmatch(expected.lower()):
        issues.append(f"{field}.sha256: expected 64 lowercase hex characters")
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        path = base_dir / path
    if not path.is_file():
        issues.append(f"{field}.path: file does not exist")
        return None
    actual = _sha256(path)
    if actual != expected.lower():
        issues.append(f"{field}.sha256: digest mismatch")
        return None
    payload = _load_json(path)
    if payload is None:
        issues.append(f"{field}.path: expected a JSON object")
        return None
    return path, actual, payload


def required_through(stage: str) -> tuple[str, ...]:
    if stage not in STAGES:
        raise ValueError(f"unknown stage: {stage}")
    required: list[str] = []
    for name, fields in STAGES.items():
        required.extend(fields)
        if name == stage:
            break
    return tuple(required)


def _validate_release(evidence: dict[str, object], base_dir: Path) -> list[str]:
    issues: list[str] = []
    raw_reports = evidence.get("qa_reports")
    if not isinstance(raw_reports, list) or len(raw_reports) != 2:
        issues.append("qa_reports: expected exactly two report references")
        reports: list[tuple[Path, str, dict[str, Any]]] = []
    else:
        reports = []
        for index, reference in enumerate(raw_reports):
            loaded = _reference(
                reference, field=f"qa_reports[{index}]", base_dir=base_dir, issues=issues
            )
            if loaded is not None:
                reports.append(loaded)

    artifact_hashes: list[str] = []
    reviewer_ids: list[tuple[str, str, str]] = []
    session_ids: list[str] = []
    report_hashes: list[str] = []
    for index, (_, report_hash, report) in enumerate(reports):
        report_hashes.append(report_hash)
        artifact_hash_value: str | None = None
        source = report.get("source")
        if not isinstance(source, dict):
            issues.append(f"qa_reports[{index}].source: missing")
        else:
            if str(source.get("kind", "")).lower() != "zip":
                issues.append(f"qa_reports[{index}].source.kind: expected zip")
            artifact_hash = _string(source.get("sha256"))
            if artifact_hash is None or not SHA256_RE.fullmatch(artifact_hash.lower()):
                issues.append(f"qa_reports[{index}].source.sha256: invalid")
            else:
                artifact_hash_value = artifact_hash.lower()
                artifact_hashes.append(artifact_hash_value)

        summary = report.get("summary")
        if not isinstance(summary, dict) or summary.get("decision") != "PASS":
            issues.append(f"qa_reports[{index}].summary.decision: expected PASS")

        qa_run = report.get("qa_run")
        if not isinstance(qa_run, dict):
            issues.append(f"qa_reports[{index}].qa_run: missing")
            continue
        skill = qa_run.get("skill")
        if (
            not isinstance(skill, dict)
            or skill.get("name") != QA_SKILL_NAME
            or str(skill.get("version")) != QA_SKILL_VERSION
        ):
            issues.append(
                f"qa_reports[{index}].qa_run.skill: expected {QA_SKILL_NAME}-{QA_SKILL_VERSION}"
            )
        if qa_run.get("clean_context") is not True:
            issues.append(f"qa_reports[{index}].qa_run.clean_context: expected true")
        if (
            qa_run.get("input_kind") != "zip"
            or qa_run.get("artifact_sha256") != artifact_hash_value
        ):
            issues.append(f"qa_reports[{index}].qa_run: provenance does not match source")
        reviewer = qa_run.get("reviewer")
        if not isinstance(reviewer, dict):
            issues.append(f"qa_reports[{index}].qa_run.reviewer: missing")
            continue
        provider = _string(reviewer.get("provider"))
        model = _string(reviewer.get("model"))
        version = _string(reviewer.get("version"))
        session_id = _string(reviewer.get("session_id"))
        if provider is None or model is None or version is None:
            issues.append(
                f"qa_reports[{index}].qa_run.reviewer: provider, model and version are required"
            )
        else:
            reviewer_ids.append((provider.lower(), model.lower(), version.lower()))
        if session_id is None:
            issues.append(f"qa_reports[{index}].qa_run.reviewer.session_id: missing")
        else:
            session_ids.append(session_id)

    if len(artifact_hashes) == 2 and len(set(artifact_hashes)) != 1:
        issues.append("qa_reports: artifact sha256 values differ")
    if len(reviewer_ids) == 2 and len(set(reviewer_ids)) != 2:
        issues.append("qa_reports: reviewer provider/model/version must identify two different AIs")
    if len(session_ids) == 2 and len(set(session_ids)) != 2:
        issues.append("qa_reports: session_id values must differ")

    consensus_ref = _reference(
        evidence.get("qa_consensus"), field="qa_consensus", base_dir=base_dir, issues=issues
    )
    if consensus_ref is not None:
        _, _, consensus = consensus_ref
        if consensus.get("skill") != {"name": QA_SKILL_NAME, "version": QA_SKILL_VERSION}:
            issues.append(
                f"qa_consensus.skill: expected {QA_SKILL_NAME}-{QA_SKILL_VERSION}"
            )
        if consensus.get("status") != "PASS":
            issues.append("qa_consensus.status: expected PASS")
        validation_errors = consensus.get("validation_errors")
        if not isinstance(validation_errors, list) or validation_errors:
            issues.append("qa_consensus.validation_errors: expected an empty list")
        disagreements = consensus.get("unresolved_disagreements")
        if not isinstance(disagreements, list) or disagreements:
            issues.append("qa_consensus.unresolved_disagreements: expected an empty list")
        bound_reports = consensus.get("report_sha256s")
        if (
            not isinstance(bound_reports, list)
            or len(bound_reports) != 2
            or sorted(str(item).lower() for item in bound_reports) != sorted(report_hashes)
        ):
            issues.append("qa_consensus.report_sha256s: does not bind the two QA reports")
        consensus_artifact = _string(consensus.get("artifact_sha256"))
        if (
            consensus_artifact is None
            or len(artifact_hashes) != 2
            or consensus_artifact.lower() != artifact_hashes[0]
        ):
            issues.append("qa_consensus.artifact_sha256: does not bind the reviewed ZIP")

    privacy_ref = _reference(
        evidence.get("attachment_privacy_report"),
        field="attachment_privacy_report",
        base_dir=base_dir,
        issues=issues,
    )
    if privacy_ref is not None:
        _, _, privacy = privacy_ref
        privacy_summary = privacy.get("summary")
        privacy_status = privacy.get("status")
        if isinstance(privacy_summary, dict):
            privacy_status = privacy_summary.get("status", privacy_summary.get("decision"))
        if privacy_status != "PASS":
            issues.append("attachment_privacy_report: report status must be PASS")

    manifest_ref = _reference(
        evidence.get("artifact_manifest"), field="artifact_manifest", base_dir=base_dir, issues=issues
    )
    if manifest_ref is not None:
        _, _, manifest = manifest_ref
        manifest_artifact = _string(manifest.get("artifact_sha256"))
        if (
            manifest_artifact is None
            or len(artifact_hashes) != 2
            or manifest_artifact.lower() != artifact_hashes[0]
        ):
            issues.append("artifact_manifest.artifact_sha256: does not bind the reviewed ZIP")
    return issues


def validate(stage: str, evidence: dict[str, object], base_dir: Path | None = None) -> list[str]:
    issues = [field for field in required_through(stage) if _missing(evidence.get(field))]
    if stage == "release":
        release_fields = set(STAGES["release"])
        issues = [issue for issue in issues if issue not in release_fields]
        issues.extend(_validate_release(evidence, base_dir or Path.cwd()))
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=tuple(STAGES))
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        parser.error("evidence must be a JSON object")
    issues = validate(args.stage, payload, args.evidence.resolve().parent)
    report = {
        "stage": args.stage,
        "status": "PASS" if not issues else "FAIL",
        "missing": issues,
        "issues": issues,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
