#!/usr/bin/env python3
"""Fail closed on incomplete dual-track run contracts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

PLACEHOLDERS = {"", "todo", "tbd", "unknown", "none", "n/a"}
REQUIRED = (
    "task_id",
    "metric_name",
    "metric_direction",
    "improvement_threshold",
    "randomness_protocol",
    "tracks",
    "development_runtime",
    "target_harness",
    "persistent_snapshot",
    "stop_loss",
    "liveness_protocol",
    "provider_failure_policy",
    "durable_evaluator_reconciliation",
)


def missing_or_placeholder(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip().lower() in PLACEHOLDERS)


def validate(contract: dict[str, object]) -> list[str]:
    errors = [f"missing or placeholder: {key}" for key in REQUIRED if missing_or_placeholder(contract.get(key))]
    tracks = contract.get("tracks")
    if not isinstance(tracks, list) or len(tracks) != 2 or len(set(map(str, tracks))) != 2:
        errors.append("tracks must contain exactly two distinct roles")
    if contract.get("metric_direction") not in {"minimize", "maximize"}:
        errors.append("metric_direction must be minimize or maximize")
    threshold = contract.get("improvement_threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
        errors.append("improvement_threshold must be a positive number")
    if contract.get("requires_gpu") is True and missing_or_placeholder(contract.get("backend_gpu_evidence")):
        errors.append("GPU runs require backend_gpu_evidence for the target version")
    if contract.get("server_provides_docker") is True:
        for key in ("docker_host_preflight", "agent_image_probe", "verifier_image_probe"):
            if missing_or_placeholder(contract.get(key)):
                errors.append(f"built-in Docker requires {key}")
        if contract.get("requires_gpu") is True and missing_or_placeholder(contract.get("container_gpu_probe")):
            errors.append("GPU Docker runs require container_gpu_probe")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("contract", type=Path)
    args = parser.parse_args()
    contract = json.loads(args.contract.read_text(encoding="utf-8"))
    errors = validate(contract)
    if errors:
        for error in errors:
            print(f"FAIL: {error}")
        return 1
    print("PASS: run contract is complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
