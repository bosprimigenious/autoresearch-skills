#!/usr/bin/env python3
"""Audit multiple AutoResearch artifacts and build a Markdown review summary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys


SUCCESS_DECISIONS = {"PASS", "PRECHECK-PASS", "GO-STATIC-ONLY"}
EXPECTED_CHECK_IDS = tuple(f"QA{index:02d}" for index in range(1, 22))


def slugify(value: str, index: int) -> str:
    stem = value[:-4] if value.lower().endswith(".zip") else value
    slug = re.sub(r"[^a-z0-9]+", "-", stem.lower()).strip("-")[:64]
    return f"{index:02d}-{slug or 'artifact'}"


def discover_artifacts(source_dir: Path, includes: list[str]) -> tuple[list[Path], list[str]]:
    candidates: list[Path] = []
    skipped: list[str] = []
    include_set = set(includes)
    for path in sorted(source_dir.iterdir(), key=lambda item: item.name.casefold()):
        if path.name.startswith(".") or (include_set and path.name not in include_set):
            continue
        if path.is_file() and path.suffix.lower() == ".zip":
            candidates.append(path)
        elif path.is_dir():
            candidates.append(path)
        else:
            skipped.append(path.name)
    missing = sorted(include_set - {path.name for path in candidates})
    if missing:
        raise ValueError(f"requested artifacts not found or unsupported: {', '.join(missing)}")
    return candidates, skipped


def strict_status(check: dict, adjustments: dict[str, dict]) -> str:
    adjustment = adjustments.get(check["id"])
    return adjustment["from"].upper() if adjustment else check["status"].upper()


def validate_report(report: dict) -> list[str]:
    """Verify that every checklist conclusion is present and self-consistent."""
    issues: list[str] = []
    checks = report.get("checks")
    if not isinstance(checks, list):
        return ["checks is not a list"]
    ids = [item.get("id") for item in checks if isinstance(item, dict)]
    missing = sorted(set(EXPECTED_CHECK_IDS) - set(ids))
    duplicates = sorted({check_id for check_id in ids if ids.count(check_id) > 1})
    unexpected = sorted(set(ids) - set(EXPECTED_CHECK_IDS))
    if missing:
        issues.append("missing checks: " + ", ".join(missing))
    if duplicates:
        issues.append("duplicate checks: " + ", ".join(duplicates))
    if unexpected:
        issues.append("unexpected checks: " + ", ".join(str(value) for value in unexpected))
    required_fields = {"id", "title", "status", "severity", "summary", "evidence", "remediation"}
    valid_statuses = {"pass", "fail", "warn", "manual", "not_applicable"}
    for item in checks:
        if not isinstance(item, dict):
            issues.append("non-object check entry")
            continue
        absent = sorted(required_fields - set(item))
        if absent:
            issues.append(f"{item.get('id', '?')} missing fields: {', '.join(absent)}")
        if item.get("status") not in valid_statuses:
            issues.append(f"{item.get('id', '?')} invalid status: {item.get('status')}")
        if not str(item.get("summary", "")).strip():
            issues.append(f"{item.get('id', '?')} has empty conclusion")
    decision = report.get("summary", {}).get("decision")
    if decision in SUCCESS_DECISIONS and any(item.get("status") == "fail" for item in checks):
        issues.append(f"success decision {decision} contains failed checklist items")
    if report.get("policy", {}).get("name") == "implementation" or decision == "PASS":
        if report.get("schema_version") != 4:
            issues.append("implementation report requires schema_version 4")
        gates = report.get("content_gates", {}).get("checks", [])
        if (not isinstance(gates, list) or len(gates) != 3
                or not all(isinstance(row, dict) for row in gates)
                or {row.get("id") for row in gates} != {"G01", "G02", "G03"}):
            issues.append("implementation report requires G01–G03 exactly once")
            gates = []
        if decision == "PASS":
            if any(row.get("status") != "pass" for row in gates):
                issues.append("PASS contains unpassed content gates")
            if any(row.get("status") in ("manual", "warn") for row in checks):
                issues.append("PASS contains unfinished checklist items")
            if report.get("review", {}).get("completed") is not True:
                issues.append("PASS requires completed review")
            if not isinstance(report.get("runtime_review"), dict) or report["runtime_review"].get("status") != "pass":
                issues.append("PASS requires both effective trajectory durations reviewed")
            comparison = report.get("overview", {}).get("baseline_reference", {})
            if not comparison.get("baseline_method") or not comparison.get("reference_method"):
                issues.append("PASS requires Baseline and Reference method introductions")
            harbor = report.get("harbor")
            if not isinstance(harbor, dict):
                issues.append("PASS requires reviewed Harbor and Docker path contract")
            else:
                harbor_checks = harbor.get("checks")
                valid_harbor = (isinstance(harbor_checks, list) and len(harbor_checks) == 6
                                and all(isinstance(row, dict) for row in harbor_checks)
                                and {row.get("id") for row in harbor_checks} == {f"H{i:02d}" for i in range(1, 7)})
                if not valid_harbor:
                    issues.append("PASS requires H01–H06 exactly once")
                    by_id = {}
                else:
                    by_id = {row["id"]: row for row in harbor_checks}
                    if any(by_id[key].get("status") != "pass" for key in ("H01", "H02", "H03", "H04")):
                        issues.append("PASS requires H01–H04 pass; QA17 cannot override Harbor")
                    if by_id["H05"].get("status") not in ("pass", "not_applicable"):
                        issues.append("PASS contains unpassed H05 Harbor check")
                    if by_id["H06"].get("status") != "pass" or not by_id["H06"].get("evidence"):
                        issues.append("PASS requires mandatory NOP evidence and H06 pass")
                    expected_runtime = {"pass": "evidence_consistent"}.get(by_id["H06"].get("status"))
                    if harbor.get("runtime_status") != expected_runtime:
                        issues.append("Harbor runtime_status disagrees with H06")
                if harbor.get("static_status") != "pass" or harbor.get("qa17_status") != "pass":
                    issues.append("PASS requires passing Harbor static/QA17 statuses")
                if harbor.get("runtime_status") != "evidence_consistent":
                    issues.append("PASS requires completed NOP runtime evidence")
                trial = harbor.get("trial_evidence")
                if not isinstance(trial, dict) or trial.get("agent") != "nop":
                    issues.append("PASS requires a reviewed NOP Trial")
                if harbor.get("runtime_status") == "not_run":
                    h06 = by_id.get("H06", {})
                    if h06.get("evidence"):
                        issues.append("H06 cannot skip supplied runtime evidence")
                artifacts = harbor.get("artifact_contract")
                if not isinstance(artifacts, dict) or artifacts.get("status") != "pass":
                    issues.append("PASS requires consistent submission artifact paths")
                hidden = harbor.get("hidden_review")
                if not isinstance(hidden, dict) or hidden.get("status") != "pass":
                    issues.append("PASS requires reviewed Hidden supply and grading-use evidence")
                contract = harbor.get("path_contract")
                if not isinstance(contract, dict):
                    issues.append("PASS requires Docker path_contract")
                else:
                    findings = contract.get("findings", [])
                    if not isinstance(findings, list) or any(isinstance(row, dict) and row.get("status") == "fail" for row in findings):
                        issues.append("PASS contains failed Docker path findings")
                    status = contract.get("status")
                    if status == "manual":
                        # Harbor preserves the static manual observation after a
                        # valid semantic resolution; H03 is its final conclusion.
                        resolution = contract.get("manual_resolution")
                        resolution_valid = (isinstance(resolution, dict)
                            and isinstance(resolution.get("summary"), str) and bool(resolution["summary"].strip())
                            and isinstance(resolution.get("evidence"), list) and bool(resolution["evidence"])
                            and all(isinstance(ref, str) and ref.strip() for ref in resolution["evidence"]))
                        if not resolution_valid or by_id.get("H03", {}).get("status") != "pass" or (contract.get("profile") == "custom" and not contract.get("adapter_evidence")):
                            issues.append("PASS contains unresolved manual Docker path contract")
                    elif status not in ("pass", "not_applicable"):
                        issues.append("PASS contains failed/unknown Docker path contract; QA17 cannot override it")
    return issues


def render_summary(summary: dict) -> str:
    if summary["policy"] != "strict":
        lines = [f"# 批量质检结论：{summary['pass_count']}/{summary['artifact_count']} 个产物通过", "",
                 "各产物需完成逐项语义复核后才可通过；初次收集结果为未完成检查。", "",
                 "| 产物 | 结论 | 报告 |", "|---|---|---|"]
        labels = {"PASS": "通过", "FAIL": "不通过", "INCOMPLETE": "未完成检查"}
        for row in summary["artifacts"]:
            name = row['name'].replace('|', '\\|')
            lines.append(f"| {name} | {labels.get(row['decision'], row['decision'])} | [TXT]({row['slug']}/report.txt) / [MD]({row['slug']}/report.md) / [JSON]({row['slug']}/report.json) |")
        return "\n".join(lines) + "\n"
    lines = [
        "# AutoResearch 批量质检复核报告",
        "",
        f"- 质检策略：**{summary['policy']}**",
        f"- 案例数：{summary['artifact_count']}",
        f"- 通过数：**{summary['pass_count']}**",
        f"- 目标：至少 {summary['minimum_passes']} 个产物通过",
        f"- 目标达成：**{'YES' if summary['target_met'] else 'NO'}**",
        "",
        "> 每个产物均生成独立 report.txt/report.md/report.json。宽松预检中的 PRECHECK-PASS 不等于 strict 交付认证。",
        "",
        "## 结果总览",
        "",
        "| # | 产物 | 结论 | 21 项复核 | P/F/W/M | 严格失败 | 详细报告 |",
        "|---:|---|---|---|---:|---:|---|",
    ]
    for index, item in enumerate(summary["artifacts"], 1):
        if item.get("report"):
            counts = item["counts"]
            count_text = f"{counts.get('pass', 0)}/{counts.get('fail', 0)}/{counts.get('warn', 0)}/{counts.get('manual', 0)}"
            link = f"[TXT](./{item['slug']}/report.txt) / [MD](./{item['slug']}/report.md) / [JSON](./{item['slug']}/report.json)"
            lines.append(
                f"| {index} | `{item['name']}` | **{item['decision']}** | "
                f"{'完整' if not item['review_issues'] else '异常'} | {count_text} | "
                f"{item['strict_failures']} | {link} |"
            )
        else:
            lines.append(f"| {index} | `{item['name']}` | ERROR | 异常 | - | - | {item['error']} |")

    lines.extend(["", "## 逐产物、21 项结论复核", ""])
    for index, item in enumerate(summary["artifacts"], 1):
        lines.extend([f"### {index}. {item['name']}", ""])
        if not item.get("report"):
            lines.extend([f"- 审计错误：{item['error']}", ""])
            continue
        report = item["report"]
        adjustments = {
            adjustment["id"]: adjustment
            for adjustment in report.get("policy", {}).get("adjustments", [])
        }
        lines.extend(
            [
                f"- 当前结论：**{item['decision']}**",
                f"- 21 项复核：**{'完整' if not item['review_issues'] else '异常'}**",
                f"- 严格失败项：{item['strict_failures']}",
                f"- 独立报告：[report.txt](./{item['slug']}/report.txt) / [report.md](./{item['slug']}/report.md)",
                "",
                "| ID | 宽松状态 | strict 状态 | 结论摘要 |",
                "|---|---|---|---|",
            ]
        )
        for check in report.get("checks", []):
            summary_text = str(check.get("summary", "")).replace("|", "\\|").replace("\n", " ")
            lines.append(
                f"| {check['id']} | {check['status'].upper()} | "
                f"{strict_status(check, adjustments)} | {summary_text} |"
            )
        lines.append("")
        if item["review_issues"]:
            lines.append("复核异常：")
            lines.append("")
            lines.extend(f"- {issue}" for issue in item["review_issues"])
            lines.append("")

    if summary.get("skipped"):
        lines.extend(["## 未审计项", ""])
        lines.extend(f"- `{name}`：当前批量入口仅处理 ZIP 和目录。" for name in summary["skipped"])
        lines.append("")
    return "\n".join(lines)


def audit_collection(
    source_dir: Path,
    out_dir: Path,
    policy: str,
    minimum_passes: int,
    includes: list[str],
) -> dict:
    source_dir = source_dir.expanduser().resolve()
    out_dir = out_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"source directory does not exist: {source_dir}")
    artifacts, skipped = discover_artifacts(source_dir, includes)
    if not artifacts:
        raise ValueError("no ZIP or directory artifacts found")
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_script = Path(__file__).with_name("audit_task.py")
    records: list[dict] = []
    for index, artifact in enumerate(artifacts, 1):
        slug = slugify(artifact.name, index)
        artifact_out = out_dir / slug
        process = subprocess.run(
            [
                sys.executable,
                str(audit_script),
                str(artifact),
                "--out-dir",
                str(artifact_out),
                "--policy",
                policy,
                "--fail-on",
                "never",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        json_path = artifact_out / "report.json"
        if process.returncode == 0 and json_path.is_file():
            report = json.loads(json_path.read_text(encoding="utf-8"))
            review_issues = validate_report(report)
            decision = report["summary"]["decision"] if not review_issues else "INVALID-REPORT"
            records.append(
                {
                    "name": artifact.name,
                    "source": artifact.name,
                    "slug": slug,
                    "decision": decision,
                    "counts": report["summary"]["counts"],
                    "strict_failures": len(report.get("policy", {}).get("adjustments", []))
                    + report["summary"]["counts"].get("fail", 0),
                    "review_issues": review_issues,
                    "report": report,
                }
            )
        else:
            error = (process.stderr or process.stdout or "unknown audit error").strip()
            records.append(
                {
                    "name": artifact.name,
                    "source": artifact.name,
                    "slug": slug,
                    "decision": "ERROR",
                    "error": error,
                }
            )
    pass_count = sum(item["decision"] in SUCCESS_DECISIONS for item in records)
    summary = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_dir": ".",
        "policy": policy,
        "minimum_passes": minimum_passes,
        "artifact_count": len(records),
        "pass_count": pass_count,
        "target_met": pass_count >= minimum_passes,
        "skipped": skipped,
        "artifacts": records,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rendered = render_summary(summary)
    (out_dir / "summary.md").write_text(rendered, encoding="utf-8")
    (out_dir / "summary.txt").write_text(rendered, encoding="utf-8")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path, help="directory containing artifact ZIPs/directories")
    parser.add_argument("--out-dir", type=Path, default=Path("qa-collection-report"))
    parser.add_argument("--policy", choices=("implementation", "precheck", "strict"), default="implementation")
    parser.add_argument("--min-pass", type=int, default=3)
    parser.add_argument(
        "--include",
        action="append",
        default=[],
        help="exact top-level artifact filename; repeat to select multiple",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.min_pass < 1:
        print("--min-pass must be positive", file=sys.stderr)
        return 2
    try:
        summary = audit_collection(
            args.source_dir,
            args.out_dir,
            args.policy,
            args.min_pass,
            args.include,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"collection audit failed safely: {exc}", file=sys.stderr)
        return 2
    print(
        json.dumps(
            {
                "target_met": summary["target_met"],
                "pass_count": summary["pass_count"],
                "artifact_count": summary["artifact_count"],
                "summary_txt": "summary.txt",
                "summary_md": "summary.md",
            },
            ensure_ascii=False,
        )
    )
    return 0 if summary["target_met"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
