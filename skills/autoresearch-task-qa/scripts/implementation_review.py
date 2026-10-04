#!/usr/bin/env python3
"""Safe evidence collection and reviewed implementation-level Markdown reports.

Semantic conclusions are supplied by the skill, not guessed from keyword counts.
No submission code is imported or executed. Only this skill's legacy ZIP reader
is reused. Runtime and report consistency checks are deterministic.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shutil
import tempfile

from harbor_review import collect as collect_harbor, apply_review as apply_harbor_review, summary as harbor_summary
from submission_format import collect as collect_submission_format, validate_trajectory
from content_gates import empty as empty_content_gates, apply_review as apply_content_review, assess_runtime

TITLES = [
    "八章节与题面信息泄露", "评分公式与 0/1 锚点", "参考解分数与复现材料",
    "波动指标的 3σ 可分辨性", "约束执行实现", "Verifier 入口、分数与错误处理",
    "instruction 中的 Hidden 信息泄露", "instruction 中的参考解信息泄露",
    "结果清理与原子写入", "冻结面保护或恢复实现", "instruction 八项内容",
    "Git 清理", "Baseline 与参考解提升", "合法改进空间", "资源限制（不检查）",
    "轨迹实际迭代时长", "Harbor Harness 兼容性", "轨迹、日志与分析",
    "seed 与有效提升阈值", "修改范围、网络与工具策略", "三个 JSON 的填写内容",
]
IDS = [f"QA{i:02d}" for i in range(1, 22)]
LIMIT = 4 * 1024 * 1024
MIN_ITERATION_SECONDS = 10 * 3600
QA_SKILL_NAME = "autoresearch-qa-skills"
QA_SKILL_VERSION = "0.3.4"
OVERVIEW_DIRECTIONS = {"minimize", "maximize", "unknown"}
TRAJECTORY_STATUSES = {"complete", "partial", "missing", "unreadable"}
FORMAT_STATUSES = {"aligned", "aligned_with_extras", "deviations", "manual"}
FORMAT_CLASSIFICATIONS = {"extra_allowed", "merge_candidate", "misplaced", "obsolete_layout"}


def read_json_records(path):
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def numeric(value):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) and number >= 0 else None
    except (ValueError, TypeError):
        return None


def timestamp(value):
    if isinstance(value, (float, int)) and not isinstance(value, bool):
        return numeric(value)
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d\d-\d\d[T ]", value):
        return None
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        # Naive timestamps in one run are interpreted in the same local clock.
        return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
    except ValueError:
        return None


def trajectory_durations(root, files):
    """Use one run's actual duration, never sum runs or read budget/uptime fields."""
    results, errors = [], []
    for rel in files:
        path = Path(rel)
        if path.suffix.lower() not in (".json", ".jsonl"):
            continue
        if not ("trajectory" in path.name.lower() or path.name in ("run.json", "run_summary.json")):
            continue
        try:
            if (root / rel).stat().st_size > LIMIT:
                errors.append(f"{rel}: 文件超过自动解析限制，需读取运行摘要")
                continue
            data = read_json_records(root / rel)
        except (OSError, ValueError, UnicodeError) as exc:
            errors.append(f"{rel}: {type(exc).__name__}")
            continue
        events = {}

        def visit(node, trail="", run_id=None, run_level=True):
            if isinstance(node, list):
                for i, value in enumerate(node):
                    visit(value, f"{trail}[{i}]", run_id, run_level)
                return
            if not isinstance(node, dict):
                return
            run_id = str(node.get("run_id", node.get("session_id", run_id or "")))
            is_trial = any(k in node for k in ("trial_id", "trial", "iteration", "step"))
            eligible = run_level and not is_trial
            # Only explicit run-level durations count; generic trial timing does not.
            if eligible:
                for key, factor in (("run_duration_seconds", 1), ("wall_time_seconds", 1),
                                    ("duration_seconds", 1), ("elapsed_seconds", 1),
                                    ("duration_hours", 3600)):
                    value = numeric(node.get(key))
                    if value is not None:
                        results.append({"seconds": value * factor, "evidence": f"{rel}#{trail}.{key}", "run_id": run_id})
                        break
                start = next((timestamp(node[k]) for k in ("started_at", "start_time", "start_timestamp") if k in node), None)
                end = next((timestamp(node[k]) for k in ("ended_at", "end_time", "end_timestamp", "finished_at") if k in node), None)
                if start is not None and end is not None:
                    if end >= start:
                        results.append({"seconds": end - start, "evidence": f"{rel}#{trail}:start/end", "run_id": run_id})
                    else:
                        errors.append(f"{rel}#{trail}: 结束时间早于开始时间")
            event = str(node.get("event", node.get("type", ""))).lower()
            if run_id and event in ("run_start", "run_end", "session_start", "session_end"):
                point = timestamp(node.get("timestamp", node.get("time")))
                if point is not None:
                    events.setdefault(run_id, {}).setdefault("start" if event.endswith("start") else "end", []).append(point)
            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    # Nested run containers are recognized, arbitrary timings/budgets aren't.
                    nested_run = key in ("runs", "agent_results", "trajectories", "sessions", "run", "metadata", "run_metadata")
                    visit(value, f"{trail}.{key}", run_id, nested_run)

        visit(data)
        for run_id, endpoints in events.items():
            starts, ends = endpoints.get("start", []), endpoints.get("end", [])
            if len(starts) == len(ends) == 1 and ends[0] >= starts[0]:
                results.append({"seconds": ends[0] - starts[0], "evidence": f"{rel}#run_id={run_id}:events", "run_id": run_id})
    return results, errors


def item(check_id, status="manual", reason="等待逐项阅读并复核实现内容。", evidence=None):
    return {"id": check_id, "title": TITLES[IDS.index(check_id)], "status": status,
            "severity": "info" if status in ("pass", "not_applicable") else "medium",
            "summary": reason, "evidence": evidence or [], "remediation": "", "acceptance_evidence": ""}


def finish(report):
    counts = {s: sum(c["status"] == s for c in report["checks"])
              for s in ("pass", "fail", "manual", "warn", "not_applicable")}
    gates = report.get("content_gates", empty_content_gates())["checks"]
    gate_counts = {s: sum(c["status"] == s for c in gates) for s in ("pass", "fail", "manual")}
    # Format collection includes heuristic observations and equivalent layouts.
    # Confirmed material gaps enter G03/QA03/QA13/H01/H03 through semantic review.
    harbor_status = report.get("harbor", {}).get("qa17_status")
    harbor_failure = harbor_status == "fail" and not any(c["id"] == "QA17" and c["status"] == "fail" for c in report["checks"])
    blockers = counts["fail"] + gate_counts["fail"] + int(harbor_failure)
    incomplete = bool(report.get("inspection_error") or counts["manual"] or gate_counts["manual"])
    decision = "FAIL" if blockers else "INCOMPLETE" if incomplete else "PASS"
    report["summary"] = {"decision": decision, "counts": counts, "gate_counts": gate_counts,
                         "blockers": blockers}
    report.setdefault("review", {})["completed"] = not incomplete
    return report


def empty_overview():
    return {
        "optimization_surface": {
            "summary": "等待逐项阅读后概括任务的可优化方法面。",
            "modifiable": [],
            "fixed": [],
            "metric_and_gate": "等待确认主指标、方向与质量门。",
            "evidence": [],
        },
        "baseline_reference": {
            "baseline_method": "等待阅读 Baseline 实现并介绍方法与训练配置。",
            "reference_method": "等待阅读 Reference 实现并介绍方法改动。",
            "metric": "unknown",
            "direction": "unknown",
            "paired_runs": [],
            "baseline_mean": None,
            "baseline_sample_std": None,
            "reference_mean": None,
            "reference_sample_std": None,
            "improvement_summary": "等待核对正式 seed 的原始结果与汇总。",
            "quality_gate_summary": "等待核对质量门。",
            "evidence": [],
        },
        "trajectories": [
            {"name": "轨迹 1", "status": "missing", "source_path": None,
             "packaged_rounds": None, "reported_effective_rounds": None,
             "duration_hours": None, "summary": "等待检查第一条轨迹。",
             "final_result": "无法确认。", "evidence": []},
            {"name": "轨迹 2", "status": "missing", "source_path": None,
             "packaged_rounds": None, "reported_effective_rounds": None,
             "duration_hours": None, "summary": "等待检查第二条轨迹。",
             "final_result": "无法确认。", "evidence": []},
        ],
    }


def printable_number(value):
    if value is None:
        return "-"
    return f"{value:.12g}" if isinstance(value, float) else str(value)


def compact_markdown(report):
    labels = {"PASS": "通过", "FAIL": "不通过", "INCOMPLETE": "未完成检查"}
    statuses = {"pass": "通过", "fail": "不通过", "manual": "未完成检查", "not_applicable": "不适用"}
    trajectory_labels = {"complete": "完整", "partial": "部分", "missing": "缺失", "unreadable": "无法读取"}
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    overview = report.get("overview") or empty_overview()
    surface = overview.get("optimization_surface", {})
    comparison = overview.get("baseline_reference", {})
    direction_label = {"minimize": "越低越好", "maximize": "越高越好", "unknown": "方向待确认"}.get(comparison.get("direction"), "方向待确认")
    lines = ["# 优化面介绍", "", cell(surface.get("summary", "无法确认优化面。")), ""]
    if surface.get("modifiable"):
        lines.append("可修改：" + "；".join(cell(x) for x in surface["modifiable"]) + "。")
    else:
        lines.append("可修改：无法确认。")
    lines.append("")
    if surface.get("fixed"):
        lines.append("固定面：" + "；".join(cell(x) for x in surface["fixed"]) + "。")
    else:
        lines.append("固定面：无法确认。")
    lines.extend(["", "指标与质量门：" + cell(surface.get("metric_and_gate", "无法确认。")), "",
                  "# Baseline 与 Reference 跑分", "",
                  "Baseline 方法：" + cell(comparison.get("baseline_method", "尚未介绍。")), "",
                  "Reference 方法：" + cell(comparison.get("reference_method", "尚未介绍。")), "",
                  f"主指标：{cell(comparison.get('metric', 'unknown'))}（{direction_label}）。", ""])
    paired = comparison.get("paired_runs") or []
    if paired:
        lines.extend(["| training_seed | Baseline | Reference | 配对改善 | 有效性 |", "|---|---:|---:|---:|---|"])
        for row in paired:
            baseline, reference = row.get("baseline"), row.get("reference")
            improvement = None
            if baseline is not None and reference is not None:
                improvement = baseline - reference if comparison.get("direction") == "minimize" else reference - baseline if comparison.get("direction") == "maximize" else None
            valid = f"B:{'是' if row.get('baseline_valid') is True else '否' if row.get('baseline_valid') is False else '待确认'} / R:{'是' if row.get('reference_valid') is True else '否' if row.get('reference_valid') is False else '待确认'}"
            lines.append(f"| {cell(row.get('seed', '-'))} | {printable_number(baseline)} | {printable_number(reference)} | {printable_number(improvement)} | {valid} |")
    else:
        lines.append("未找到可列出的正式 seed 成对跑分。")
    lines.extend(["", "汇总：Baseline 均值 " + printable_number(comparison.get("baseline_mean"))
                  + "，样本标准差 " + printable_number(comparison.get("baseline_sample_std"))
                  + "；Reference 均值 " + printable_number(comparison.get("reference_mean"))
                  + "，样本标准差 " + printable_number(comparison.get("reference_sample_std")) + "。",
                  "", "改善结论：" + cell(comparison.get("improvement_summary", "无法确认。")),
                  "", "质量门：" + cell(comparison.get("quality_gate_summary", "无法确认。")), "",
                  "# 前置内容门槛", "", "| 门槛 | 结论 | 原因 |", "|---|---|---|"])
    for gate in report.get("content_gates", empty_content_gates())["checks"]:
        lines.append(f"| {gate['id']} {cell(gate['title'])} | {statuses[gate['status']]} | {cell(gate['summary'])} |")
        for warning in gate.get("warnings", []):
            lines.extend(["", "复核建议：" + cell(warning)])
    g03 = next((gate for gate in report.get("content_gates", {}).get("checks", []) if gate.get("id") == "G03"), {})
    computed = g03.get("computed", {})
    assessment = g03.get("assessment") if isinstance(g03.get("assessment"), dict) else {}
    def computed_number(value):
        return "待确认" if value is None else printable_number(value)
    lines.extend(["", "G03 复算：Δ=" + computed_number(computed.get("improvement"))
                  + "；σ_B（样本标准差）=" + computed_number(computed.get("baseline_sample_std"))
                  + "；U=" + computed_number(assessment.get("upper_bound"))
                  + "；归一化分数=" + computed_number(computed.get("normalized_score")) + "。"])
    lines.extend(["", "# 两条轨迹迭代概况", "",
                  "| Agent 轨迹 | 状态 | 打包轮数 | 声明有效轮数 | 时长 | 概况与最终结果 |",
                  "|---|---|---:|---:|---:|---|"])
    for trajectory in overview.get("trajectories", [])[:2]:
        duration = trajectory.get("duration_hours")
        duration_text = "-" if duration is None else f"{duration:.2f}h"
        first = cell(trajectory.get("summary", "无法确认。")).rstrip("；。 ")
        detail = first + "；" + cell(trajectory.get("final_result", "无法确认。"))
        lines.append(f"| {cell(trajectory.get('name', '未命名'))} | {trajectory_labels.get(trajectory.get('status'), '待确认')} | {printable_number(trajectory.get('packaged_rounds'))} | {printable_number(trajectory.get('reported_effective_rounds'))} | {duration_text} | {detail} |")
    if len(overview.get("trajectories", [])) < 2:
        lines.append("| 第二条轨迹 | 缺失 | - | - | - | 未提供或尚未完成复核。 |")

    format_alignment = report.get("format_alignment", {})
    format_review = format_alignment.get("review", {})
    lines.extend(["", "# 格式对齐建议", "", cell(format_review.get("summary") or format_alignment.get("summary") or "等待检查新版提交格式。")])
    format_suggestions = []
    for key in ("suggestions", "extras"):
        value = format_alignment.get(key, [])
        if isinstance(value, list):
            format_suggestions.extend(value)
    manual_suggestions = format_review.get("additional_suggestions", []) if isinstance(format_review.get("additional_suggestions", []), list) else []
    # A semantic reviewer may refine the collector's generic classification.
    # Keep one row per path, with the reviewed version taking precedence.
    by_path = {row.get("path"): row for row in format_suggestions if isinstance(row, dict) and row.get("path")}
    by_path.update({row.get("path"): row for row in manual_suggestions if isinstance(row, dict) and row.get("path")})
    format_suggestions = list(by_path.values())
    classification_labels = {"extra_allowed": "可保留的额外项", "merge_candidate": "可合并",
                             "misplaced": "需归位/替换", "obsolete_layout": "旧布局/打包杂项"}
    if format_suggestions:
        lines.append("")
    for suggestion in format_suggestions:
        if not isinstance(suggestion, dict):
            continue
        classification = suggestion.get("classification", "extra_allowed")
        lines.append(f"- `{cell(suggestion.get('path', '?'))}`（{classification_labels.get(classification, cell(classification))}）：{cell(suggestion.get('recommendation', '可保留；不单独判错。'))}")
    missing_or_issues = []
    for key in ("missing", "issues", "deviations"):
        value = format_alignment.get(key, [])
        if isinstance(value, list):
            missing_or_issues.extend(value)
    for issue in missing_or_issues:
        if isinstance(issue, dict):
            lines.append("- 格式缺口：" + cell(issue.get("summary") or issue.get("message") or issue.get("path") or issue))
        else:
            lines.append("- 格式缺口：" + cell(issue))
    if format_suggestions:
        lines.extend(["", "说明：额外材料仅作精简或归位建议，不因多交而单独判错。"])

    counts = report["summary"]["counts"]
    gate_counts = report["summary"].get("gate_counts", {"pass": 0, "fail": 0, "manual": 3})
    lines.extend(["", f"# 质检结论：{labels[report['summary']['decision']]}", "",
                  f"产物：{cell(Path(report['source']['path']).name)}｜通过 {counts['pass']} 项｜不通过 {counts['fail']} 项｜未完成 {counts['manual']} 项｜跳过/不适用 {counts['not_applicable']} 项", "",
                  f"前置三门：通过 {gate_counts['pass']} 项｜不通过 {gate_counts['fail']} 项｜待核验 {gate_counts['manual']} 项；全部复核完成：{'是' if report.get('review', {}).get('completed') else '否'}。", "",
                  "口径：只读实现情况检查；QA07/08 仅查 instruction，QA15 跳过，QA16 默认要求两条轨迹各至少 10h 有效迭代；仅完全不涉及训练或微调且单轮迭代很短的任务，才可凭资格与闭环等证据采用每条至少 7h 的例外。排队、安装与故障不计。未执行提交代码、未独立复跑参考解或反序列化模型。", "",
                  harbor_summary(report["harbor"]) if "harbor" in report else "Harbor：未检查。", "",
                  "| 检查项 | 是否通过 | 原因 |", "|---|---|---|"])
    for check in report["checks"]:
        label = "跳过" if check["id"] == "QA15" else statuses[check["status"]]
        lines.append(f"| {check['id']} {cell(check['title'])} | {label} | {cell(check['summary'])} |")
    if report.get("inspection_error"):
        lines.extend(["", "未完成原因：" + cell(report["inspection_error"])])
    if report.get("risk_notes"):
        risk_text = "；".join(cell(x).rstrip("；。 ") for x in report["risk_notes"][:3]) + "。"
        lines.extend(["", "风险提示（不额外计入清单结论）：" + risk_text])
    lines.extend(["", "# 退回专家说明", "", return_to_expert(report).rstrip()])
    return "\n".join(lines) + "\n"


def return_to_expert(report):
    """Render only concrete reviewed corrections; never fabricate missing advice."""
    findings = [row for row in report.get("content_gates", {}).get("checks", []) + report["checks"]
                if row["status"] in ("fail", "manual")]
    lines = []
    for row in findings:
        status = "不通过" if row["status"] == "fail" else "待补充核验"
        lines.append(f"{row['id']} {row['title']}（{status}）：{row['summary']}")
        if row.get("evidence"):
            lines.append("证据：" + "；".join(row["evidence"]))
        if row.get("remediation") and row.get("acceptance_evidence"):
            lines.append("请修改/补充：" + row["remediation"])
            lines.append("复检验收材料：" + row["acceptance_evidence"])
        else:
            lines.append("尚未形成具体修正与验收说明；此项仍需质检员完成复核，不能直接当作专家整改要求。")
        lines.append("")
    if report.get("inspection_error"):
        lines.extend(["质检未完成：" + report["inspection_error"], "请先修正上述材料读取或 review 结构错误，再生成可交付的质检结论。", ""])
    format_issues = report.get("format_alignment", {}).get("alignment_issues", [])
    if format_issues:
        lines.append("待质检员核实的格式观察（等价材料可以认可，不直接作为专家整改结论）：")
        lines.extend(f"- {row.get('path', '?')}：{row.get('message', '')}" for row in format_issues)
    return "\n".join(lines).rstrip() + "\n" if lines else "本次质检没有需退回的已确认问题。\n"


def build_report(source, root, files, digest=None):
    results, errors = trajectory_durations(root, files)
    checks = [item(i) for i in IDS]
    checks[14] = item("QA15", "not_applicable", "按用户要求不检查资源与单次迭代限制。")
    checks[15] = item("QA16", "manual", "等待核对两条轨迹各自的有效时长及排除时段；总历时、预算和单条最长运行不能代替。")
    return finish({"schema_version": 4, "generated_at": datetime.now(timezone.utc).isoformat(),
                   "source": {"path": source.name, "sha256": digest, "kind": "directory" if source.is_dir() else "zip"},
                   "workspace_root": ".", "policy": {"name": "implementation", "revision": "research-quality-v3", "strict_release_certification": False, "adjustments": []},
                   "overview": empty_overview(), "format_alignment": collect_submission_format(root),
                   "content_gates": empty_content_gates(), "runtime_review": None,
                   "checks": checks, "extra_checks": [], "runtime_candidates": results, "runtime_parse_errors": errors,
                   "harbor": collect_harbor(root, files),
                   "review": {"completed": False}, "risk_notes": [], "assumptions": [],
                   "limitations": ["通过仅表示存在对应实现或材料，不证明独立复现和运行安全。"]})


def inventory(root):
    root = root.resolve()
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                  if p.is_file() and not p.is_symlink() and not any(x.is_symlink() for x in p.parents)
                  and "__MACOSX" not in p.parts and not p.name.startswith("._"))


def cleanup_previous_evidence(out):
    """Remove only the collector-owned evidence directory from an earlier pass."""
    manifest = out / "inventory.json"
    if not manifest.is_file():
        return
    try:
        root_name = json.loads(manifest.read_text(encoding="utf-8"))["root"]
        previous = (out / root_name).resolve()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return
    resolved_out = out.resolve()
    if previous.parent != resolved_out or not previous.name.startswith("evidence-"):
        return
    if previous.is_dir() and not previous.is_symlink():
        shutil.rmtree(previous)


def evidence_path(root, evidence):
    name = evidence.split("#", 1)[0]
    name = re.sub(r":\d+$", "", name)
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"evidence path is not a readable file within the inspected bundle: {evidence}")
    return path


def review_evidence(root, evidence, label, required=False):
    if not isinstance(evidence, list) or not all(isinstance(ref, str) for ref in evidence):
        raise ValueError(f"{label}: evidence must be a string array")
    if required and not evidence:
        raise ValueError(f"{label}: requires concrete evidence")
    for ref in evidence:
        evidence_path(root, ref)
    return evidence


def finite_value(value, label, *, nullable=True, nonnegative=False, integer=False):
    if value is None and nullable:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError(f"{label}: must be a finite number or null")
    if nonnegative and value < 0:
        raise ValueError(f"{label}: must be nonnegative")
    if integer and int(value) != value:
        raise ValueError(f"{label}: must be an integer or null")
    return int(value) if integer else value


def nonempty_text(value, label, max_length=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ValueError(f"{label}: must be nonempty text no longer than {max_length} characters")
    return value.strip()


def validate_overview(overview, root):
    if not isinstance(overview, dict):
        raise ValueError("review.overview must be an object")
    surface = overview.get("optimization_surface")
    if not isinstance(surface, dict):
        raise ValueError("overview.optimization_surface must be an object")
    nonempty_text(surface.get("summary"), "optimization_surface.summary")
    nonempty_text(surface.get("metric_and_gate"), "optimization_surface.metric_and_gate")
    for key in ("modifiable", "fixed"):
        values = surface.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(x, str) and x.strip() for x in values):
            raise ValueError(f"optimization_surface.{key} must be a nonempty string array")
    review_evidence(root, surface.get("evidence"), "optimization_surface", required=True)

    comparison = overview.get("baseline_reference")
    if not isinstance(comparison, dict):
        raise ValueError("overview.baseline_reference must be an object")
    for field in ("baseline_method", "reference_method"):
        nonempty_text(comparison.get(field), "baseline_reference." + field)
    nonempty_text(comparison.get("metric"), "baseline_reference.metric", 200)
    if comparison.get("direction") not in OVERVIEW_DIRECTIONS:
        raise ValueError("baseline_reference.direction is unsupported")
    pairs = comparison.get("paired_runs")
    if not isinstance(pairs, list):
        raise ValueError("baseline_reference.paired_runs must be an array")
    seen_seeds = set()
    for index, row in enumerate(pairs):
        if not isinstance(row, dict) or not isinstance(row.get("seed"), (str, int)) or isinstance(row.get("seed"), bool):
            raise ValueError(f"baseline_reference.paired_runs[{index}]: invalid seed")
        seed_key = str(row["seed"])
        if seed_key in seen_seeds:
            raise ValueError("baseline_reference.paired_runs contains duplicate seeds")
        seen_seeds.add(seed_key)
        finite_value(row.get("baseline"), f"paired_runs[{index}].baseline")
        finite_value(row.get("reference"), f"paired_runs[{index}].reference")
        for key in ("baseline_valid", "reference_valid"):
            if row.get(key) is not None and not isinstance(row.get(key), bool):
                raise ValueError(f"paired_runs[{index}].{key} must be boolean or null")
    for key in ("baseline_mean", "baseline_sample_std", "reference_mean", "reference_sample_std"):
        finite_value(comparison.get(key), f"baseline_reference.{key}", nonnegative=key.endswith("std"))
    nonempty_text(comparison.get("improvement_summary"), "baseline_reference.improvement_summary")
    nonempty_text(comparison.get("quality_gate_summary"), "baseline_reference.quality_gate_summary")
    review_evidence(root, comparison.get("evidence"), "baseline_reference", required=bool(pairs))

    trajectories = overview.get("trajectories")
    if not isinstance(trajectories, list) or len(trajectories) != 2:
        raise ValueError("overview.trajectories must contain exactly two entries")
    for index, trajectory in enumerate(trajectories):
        label = f"trajectories[{index}]"
        if not isinstance(trajectory, dict):
            raise ValueError(f"{label} must be an object")
        nonempty_text(trajectory.get("name"), f"{label}.name", 200)
        status = trajectory.get("status")
        if status not in TRAJECTORY_STATUSES:
            raise ValueError(f"{label}.status is unsupported")
        source_path = trajectory.get("source_path")
        if source_path is not None:
            if not isinstance(source_path, str):
                raise ValueError(f"{label}.source_path must be a string or null")
            evidence_path(root, source_path)
        elif status in ("complete", "partial"):
            raise ValueError(f"{label}: complete/partial trajectory requires source_path")
        finite_value(trajectory.get("packaged_rounds"), f"{label}.packaged_rounds", nonnegative=True, integer=True)
        finite_value(trajectory.get("reported_effective_rounds"), f"{label}.reported_effective_rounds", nonnegative=True, integer=True)
        finite_value(trajectory.get("duration_hours"), f"{label}.duration_hours", nonnegative=True)
        nonempty_text(trajectory.get("summary"), f"{label}.summary")
        nonempty_text(trajectory.get("final_result"), f"{label}.final_result")
        review_evidence(root, trajectory.get("evidence"), label, required=status in ("complete", "partial"))
    return overview


def validate_format_review(format_review):
    if not isinstance(format_review, dict) or format_review.get("status") not in FORMAT_STATUSES:
        raise ValueError("format_review.status is unsupported")
    nonempty_text(format_review.get("summary"), "format_review.summary")
    suggestions = format_review.get("additional_suggestions", [])
    if not isinstance(suggestions, list):
        raise ValueError("format_review.additional_suggestions must be an array")
    for index, suggestion in enumerate(suggestions):
        if not isinstance(suggestion, dict) or not isinstance(suggestion.get("path"), str) or not suggestion["path"].strip():
            raise ValueError(f"format_review.additional_suggestions[{index}]: invalid path")
        if suggestion.get("classification") not in FORMAT_CLASSIFICATIONS:
            raise ValueError(f"format_review.additional_suggestions[{index}]: unsupported classification")
        nonempty_text(suggestion.get("recommendation"), f"format_review.additional_suggestions[{index}].recommendation")
    return format_review


def apply_review(report, review, root):
    if not isinstance(review, dict):
        raise ValueError("review must be a JSON object")
    harbor = apply_harbor_review(report["harbor"], review.get("harbor"), root)
    report["harbor"] = harbor
    report["overview"] = validate_overview(review.get("overview"), root)
    # Validate the two reviewed source paths, not only preferred filenames.
    # A handwritten pass must not override malformed or missing round evidence.
    trajectory_validation = []
    for row in report["overview"]["trajectories"]:
        if row["status"] in ("missing", "unreadable"):
            status = "fail" if row["status"] == "missing" else "manual"
            trajectory_validation.append({"path": row.get("source_path"), "status": row["status"],
                "alignment_issues": [{"status": status, "message": row["name"] + "：轨迹" + ("缺失" if status == "fail" else "不可读取"),
                                      "code": "MISSING_TRAJECTORY" if status == "fail" else "TRAJECTORY_UNREADABLE"}]})
        else:
            trajectory_validation.append(validate_trajectory(root / row["source_path"], root))
    report["format_alignment"]["reviewed_trajectories"] = trajectory_validation
    report["content_gates"] = apply_content_review(review.get("content_gates"), report["overview"], root, review_evidence)
    report["runtime_review"] = assess_runtime(
        review.get("runtime_review"), report["overview"]["trajectories"],
        report.get("runtime_candidates", []), root, review_evidence)
    report["format_alignment"]["review"] = validate_format_review(review.get("format_review"))
    rows = review.get("checks", [])
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows) or len(rows) != 21 or {row.get("id") for row in rows} != set(IDS):
        raise ValueError("review must contain QA01–QA21 exactly once")
    final = []
    for row in sorted(rows, key=lambda row: row["id"]):
        check_id, status = row["id"], row.get("status")
        if status not in ("pass", "fail", "manual", "not_applicable"):
            raise ValueError(f"{check_id}: unsupported status")
        reason, evidence = row.get("summary", ""), row.get("evidence", [])
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 220:
            raise ValueError(f"{check_id}: supply a concise reason (1–220 characters)")
        if not isinstance(evidence, list) or not all(isinstance(ref, str) for ref in evidence):
            raise ValueError(f"{check_id}: evidence must be a string array")
        if status == "not_applicable" and check_id not in ("QA04", "QA15", "QA19"):
            raise ValueError(f"{check_id}: cannot skip this checklist item")
        if status == "pass" and not evidence:
            raise ValueError(f"{check_id}: passing requires a concrete evidence reference")
        for ref in evidence:
            if ref == "@inventory" and check_id == "QA12":
                continue
            path = evidence_path(root, ref)
            if check_id in ("QA07", "QA08") and path.name.lower() != "instruction.md":
                raise ValueError(f"{check_id}: only instruction.md is in scope")
        if check_id == "QA15":
            if status != "not_applicable":
                raise ValueError("QA15 must be skipped")
            final.append(report["checks"][14])
        elif check_id == "QA16":
            runtime = report["runtime_review"]
            result = item(check_id, runtime["status"], runtime["summary"], runtime["evidence"])
            for field in ("remediation", "acceptance_evidence"):
                result[field] = runtime[field]
            final.append(result)
        elif check_id == "QA17":
            failed = [r for r in harbor["checks"] if r["status"] in ("fail", "manual")]
            if failed:
                details = "；".join(r["id"] + " " + r["title"] for r in failed)
                reason = f"Harbor 子项未通过：{details}；详细原因与证据保留在 report.json。"
            else:
                reason = harbor_summary(harbor)
            refs = list(dict.fromkeys(ref for r in harbor["checks"] for ref in r["evidence"]))
            result = item("QA17", harbor["qa17_status"], reason, refs)
            if result["status"] in ("fail", "manual"):
                defaults = {}
                if result["status"] == "fail" and len(failed) == 1 and failed[0]["id"] == "H06":
                    defaults = {
                        "remediation": "请使用当前题包版本运行一次 NOP 自检，确认构建、Trial 和独立 Verifier 正常完成，并提交运行记录。",
                        "acceptance_evidence": "同一次 NOP Trial 的配置、结果、有效 reward 和日志；当前题包版本可由已有配置、日志或专家说明关联。",
                    }
                for field in ("remediation", "acceptance_evidence"):
                    result[field] = nonempty_text(row.get(field) or defaults.get(field), "QA17." + field)
            final.append(result)
        else:
            result = item(check_id, status, reason, evidence)
            for field in ("remediation", "acceptance_evidence"):
                result[field] = row.get(field, "")
                if status in ("fail", "manual"):
                    result[field] = nonempty_text(result[field], check_id + "." + field)
            if check_id in ("QA18", "QA21"):
                defects = [issue for observation in trajectory_validation for issue in observation["alignment_issues"]]
                if defects:
                    computed = "fail" if any(x["status"] == "fail" for x in defects) else "manual"
                    if result["status"] != "fail":
                        result["status"] = computed
                    result["summary"] = "两条轨迹的八字段证据未通过：" + "；".join(x["message"] for x in defects[:2])
                    result["evidence"] = list(dict.fromkeys(evidence + [x["path"] for x in trajectory_validation if x.get("present")]))
                    result["remediation"] = "补齐两条真实轨迹，每轮八字段须完整有效；保留失败原因，不补造分数、时间或轮次。自定义状态需说明其语义。"
                    result["acceptance_evidence"] = "overview.source_path 指向两份可读取轨迹，轮次及八字段符合教程，并与运行摘要和最佳方法记录一致。"
            final.append(result)
    report["checks"] = final
    report["harbor"] = harbor
    report["risk_notes"] = review.get("risk_notes", [])
    if not isinstance(report["risk_notes"], list) or not all(isinstance(x, str) for x in report["risk_notes"]):
        raise ValueError("risk_notes must be an array of strings")
    report["review"] = {"completed": False, "method": "research gates and per-item semantic evidence review"}
    return finish(report)


def write_report(report, out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rendered = compact_markdown(report)
    (out / "report.md").write_text(rendered, encoding="utf-8")
    (out / "report.txt").write_text(rendered, encoding="utf-8")
    (out / "return_to_expert.txt").write_text(return_to_expert(report), encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--out-dir", type=Path, default=Path("qa-report"))
    parser.add_argument("--policy", choices=("implementation", "precheck"), default="implementation")
    parser.add_argument("--review", type=Path, help="per-item semantic review JSON supplied by the skill")
    parser.add_argument("--fail-on", choices=("never", "fail", "warn", "manual", "incomplete"), default="never")
    parser.add_argument("--release-self-check", action="store_true",
                        help="enforce immutable ZIP-only release QA and provenance")
    parser.add_argument("--reviewer-provider")
    parser.add_argument("--reviewer-model")
    parser.add_argument("--reviewer-version")
    parser.add_argument("--session-id")
    parser.add_argument("--clean-context", action="store_true",
                        help="attest that this new session received only the submission ZIP")
    args = parser.parse_args(argv)
    source, out = args.source.expanduser().resolve(), args.out_dir.expanduser().resolve()
    if args.release_self_check:
        missing = [flag for flag, value in (
            ("--review", args.review), ("--reviewer-provider", args.reviewer_provider),
            ("--reviewer-model", args.reviewer_model), ("--reviewer-version", args.reviewer_version),
            ("--session-id", args.session_id), ("--clean-context", args.clean_context),
        ) if not value]
        if missing:
            parser.error("release self-check requires " + ", ".join(missing))
        if not source.is_file() or source.suffix.lower() != ".zip":
            parser.error("release self-check accepts exactly one .zip submission")
        if out.exists() and (not out.is_dir() or any(out.iterdir())):
            parser.error("release self-check out-dir must be new or empty; final reports are immutable")
    # Never let report output overwrite or contaminate the submitted artifact.
    if out == source or (source.is_dir() and out.is_relative_to(source)):
        parser.error("out-dir must be outside the submitted artifact")
    report = build_report(source, out, [])
    try:
        import audit_task as legacy
        if source.is_dir():
            root, digest = source, None
        elif source.is_file():
            # Keep a bounded extracted evidence copy so the model can inspect it later.
            out.mkdir(parents=True, exist_ok=True)
            cleanup_previous_evidence(out)
            root = Path(tempfile.mkdtemp(prefix="evidence-", dir=out))
            facts = legacy.safe_extract_zip(source, root)
            if facts.unsafe_entries:
                raise ValueError("压缩包无法安全读取：" + "; ".join(facts.unsafe_entries[:3]))
            digest = facts.sha256
        else:
            raise ValueError("待检路径不存在或不是目录/ZIP")
        files = inventory(root)
        report = build_report(source, root, files, digest)
        out.mkdir(parents=True, exist_ok=True)
        inventory_root = root.name if root.parent.resolve() == out.resolve() else "."
        (out / "inventory.json").write_text(json.dumps({"root": inventory_root, "files": files}, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.review:
            report = apply_review(report, json.loads(args.review.read_text(encoding="utf-8")), root)
    except (OSError, ValueError, UnicodeError, legacy.zipfile.BadZipFile) as exc:
        report["inspection_error"] = str(exc)
        finish(report)
    if args.release_self_check:
        report["qa_run"] = {
            "skill": {"name": QA_SKILL_NAME, "version": QA_SKILL_VERSION},
            "artifact_sha256": report.get("source", {}).get("sha256"),
            "input_kind": report.get("source", {}).get("kind"),
            "clean_context": True,
            "reviewer": {
                "provider": args.reviewer_provider,
                "model": args.reviewer_model,
                "version": args.reviewer_version,
                "session_id": args.session_id,
            },
        }
    write_report(report, out)
    print(json.dumps({"decision": report["summary"]["decision"], "report_txt": "report.txt", "report_md": "report.md", "report_json": "report.json"}, ensure_ascii=False))
    if report.get("inspection_error"):
        return 2
    return int((args.release_self_check or args.fail_on != "never") and report["summary"]["decision"] != "PASS")


if __name__ == "__main__":
    raise SystemExit(main())
