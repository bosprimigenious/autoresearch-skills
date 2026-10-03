#!/usr/bin/env python3
"""Read-only collector for the simplified AutoResearch submission layout.

The collector deliberately does not import or execute anything from the inspected
package.  It only enumerates directories and reads bounded JSON/text evidence.
Missing required material is an alignment issue.  Extra material is classified
and suggested for cleanup, but never changes the alignment status by itself.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import math
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "autoresearch-submission-format.v1"
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
MAX_DISCOVERY_DEPTH = 5
MAX_DISCOVERY_DIRS = 2_000

REQUIRED_TOP_LEVEL = ("workspace", "expert_evidence", "optimization_evidence")
REQUIRED_HARBOR_DIRS = ("environment", "tests")
OPTIONAL_HARBOR_DIRS = ("solution",)
REQUIRED_HARBOR_FILES = ("instruction.md", "task.toml")
REQUIRED_RESULT_FIELDS = (
    "schema_version", "status", "role", "seed", "task_type", "method",
    "protocol", "training", "execution", "metrics", "quality_gate", "artifacts",
)
REQUIRED_TRAJECTORY_FIELDS = (
    "round", "policy_name", "method_summary", "status", "score",
    "failure_reason", "retained_best", "time",
)
SUCCESS_STATUSES = {"ok", "success", "succeeded", "pass", "passed", "complete", "completed"}
FAILURE_STATUSES = {
    "fail", "failed", "failure", "error", "invalid", "timeout", "timed_out",
    "crash", "crashed", "oom", "out_of_memory", "cancelled", "canceled",
    "exception", "nonzero_exit", "runtime_error", "compile_error", "build_error",
    "validation_error", "quality_gate_failed", "quality_gate_failure", "gate_failed",
    "gate_fail", "quality_failed", "invalid_output", "incorrect", "wrong_answer",
    "not_run", "skipped", "incomplete", "interrupted",
}

IGNORED_NAMES = {".DS_Store", "__MACOSX"}
DISCOVERY_PRUNE = {
    ".git", ".hg", ".svn", "node_modules", "model", "public_assets",
    "hidden_assets", "baseline_runs", "reference_runs",
}

MERGE_CANDIDATE_NAMES = {
    "data_manifest.json": "内容通常可并入各轮 result.json 的 protocol/data 字段。",
    "experiment_plan.json": "内容通常可并入 result.json 的 protocol 或 comparison_summary.json。",
    "train_config.yaml": "配置通常可并入每轮 result.json。",
    "train_config.yml": "配置通常可并入每轮 result.json。",
    "run_config.json": "配置通常可并入同目录的 result.json。",
    "metrics.json": "指标通常可并入同目录的 result.json。",
    "legacy_result.json": "旧结果通常可迁移并合并到 result.json。",
    "reload_metrics.json": "重载指标通常可并入 model/artifact.json。",
    "start_time.txt": "开始时间通常可并入 result.json 的 execution 字段。",
    "end_time.txt": "结束时间通常可并入 result.json 的 execution 字段。",
    "exit_code": "退出码通常可并入 result.json 的 execution 字段。",
    "console.log": "日志通常可与同轮 run.log 合并。",
    "runner.log": "日志通常可与同轮 run.log 合并。",
    "status.log": "状态通常可并入 result.json，日志并入 run.log。",
    "train.log": "训练输出通常可统一为同轮 run.log。",
}

MISPLACED_NAMES = {
    "ablation": "消融属于专家过程证据，建议移至 expert_evidence/。",
    "agent_post_validation": "Agent 最终方法复测属于 expert_evidence/。",
    "agents": "Agent 过程材料属于 expert_evidence/。",
    "best_method": "Agent 最优方法快照属于 expert_evidence/。",
    "trajectory_codex.json": "Agent 轨迹属于 expert_evidence/。",
    "trajectory_seed.json": "Agent 轨迹属于 expert_evidence/。",
    "run_summary.json": "Agent 运行汇总属于 expert_evidence/。",
    "expert_annotation.json": "专家注解属于 expert_evidence/。",
    "surface_diff.patch": "优化面代码差异更适合作为 expert_evidence/ 的可选审计附件。",
    "surface_validation.json": "修改面审计更适合作为 expert_evidence/ 的可选附件。",
}

MODEL_EXTENSIONS = {
    ".bin", ".ckpt", ".h5", ".joblib", ".keras", ".npz", ".onnx",
    ".pkl", ".pt", ".pth", ".safetensors", ".weights",
}


def _regular_file(path: Path) -> bool:
    return path.is_file() and not path.is_symlink()


def _regular_dir(path: Path) -> bool:
    return path.is_dir() and not path.is_symlink()


def _rel(path: Path, submission_root: Path) -> str:
    try:
        value = path.relative_to(submission_root).as_posix()
    except ValueError:
        value = path.as_posix()
    return value or "."


def _issue(items: list[dict[str, str]], code: str, path: str, message: str) -> None:
    issue = {"code": code, "path": path, "message": message}
    if "TRAJECTORY" in code:
        issue["status"] = "manual" if code in {"TRAJECTORY_STATUS_REVIEW", "TRAJECTORY_UNREADABLE"} else "fail"
    items.append(issue)


def _suggest(
    items: list[dict[str, str]], code: str, category: str, path: str, message: str
) -> None:
    items.append(
        {
            "code": code,
            "path": path,
            "classification": category,
            "recommendation": message,
        }
    )


def _safe_dirs(path: Path) -> list[Path]:
    try:
        entries = list(path.iterdir())
    except OSError:
        return []
    return sorted(
        (entry for entry in entries if _regular_dir(entry) and entry.name not in IGNORED_NAMES),
        key=lambda item: item.name.casefold(),
    )


def _candidate_score(path: Path) -> int:
    present = sum(_regular_dir(path / name) for name in REQUIRED_TOP_LEVEL)
    score = present * 10
    if _regular_dir(path / "workspace" / "harbor_task"):
        score += 25
    if all(_regular_dir(path / name) for name in REQUIRED_TOP_LEVEL):
        score += 50
    return score


def _locate_submission_root(root: Path) -> tuple[Path, list[str]]:
    """Find a package root below harmless ZIP wrapper directories."""
    queue: deque[tuple[Path, int]] = deque([(root, 0)])
    candidates: list[tuple[int, int, Path]] = []
    visited = 0
    while queue and visited < MAX_DISCOVERY_DIRS:
        path, depth = queue.popleft()
        visited += 1
        score = _candidate_score(path)
        if score:
            candidates.append((score, depth, path))
            # A complete root should not be searched for nested copies.
            if score >= 100:
                continue
        if depth >= MAX_DISCOVERY_DEPTH:
            continue
        for child in _safe_dirs(path):
            if child.name in DISCOVERY_PRUNE or child.name.startswith("."):
                continue
            queue.append((child, depth + 1))
    if not candidates:
        return root, []
    best_score = max(row[0] for row in candidates)
    best_depth = min(row[1] for row in candidates if row[0] == best_score)
    best = sorted(
        (row[2] for row in candidates if row[0] == best_score and row[1] == best_depth),
        key=lambda item: item.as_posix().casefold(),
    )
    return best[0], [_rel(path, root) for path in best[1:]]


def _read_json(path: Path, *, strict: bool = False) -> tuple[dict[str, Any] | None, str | None]:
    if not _regular_file(path):
        return None, "not a regular file"
    try:
        size = path.stat().st_size
        if size > MAX_JSON_BYTES:
            return None, f"JSON exceeds {MAX_JSON_BYTES} byte inspection limit"
        def reject_constant(value: str) -> None:
            raise ValueError(f"{value} is not a valid JSON number")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            **({"parse_constant": reject_constant} if strict else {}),
        )
    except (OSError, UnicodeError, ValueError) as exc:
        return None, f"{type(exc).__name__}: {exc}"
    if not isinstance(value, dict):
        return None, "top-level JSON value is not an object"
    return value, None


def _read_text(path: Path) -> str:
    if not _regular_file(path):
        return ""
    try:
        with path.open("rb") as handle:
            raw = handle.read(MAX_TEXT_BYTES + 1)
        return raw[:MAX_TEXT_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return ""


def _not_empty(path: Path) -> bool:
    try:
        return _regular_file(path) and path.stat().st_size > 0
    except OSError:
        return False


def _canonical_seed(value: Any) -> str | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if math.isfinite(value) and value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if text.lower().startswith("seed_"):
            text = text[5:]
        if not text:
            return None
        return str(int(text)) if re.fullmatch(r"[+-]?\d+", text) else text
    return None


def _task_signal(result: dict[str, Any]) -> str | None:
    for key in ("task_type", "task_kind", "run_type"):
        value = result.get(key)
        if isinstance(value, str):
            normalized = re.sub(r"[^a-z0-9]+", "_", value.casefold()).strip("_")
            if any(token in normalized for token in ("non_training", "no_training", "inference_only", "without_training")):
                return "non_training"
            if "train" in normalized or normalized in {"finetune", "fine_tuning", "model_training"}:
                return "training"
    for container in (result, result.get("protocol", {})):
        if not isinstance(container, dict):
            continue
        for key in ("is_training_task", "training_task", "requires_model", "model_required", "train_from_initialization"):
            value = container.get(key)
            if isinstance(value, bool):
                return "training" if value else "non_training"
        epochs = container.get("train_epochs")
        if isinstance(epochs, (int, float)) and not isinstance(epochs, bool) and epochs > 0:
            return "training"
    training = result.get("training")
    if isinstance(training, dict) and training:
        return "training"
    return None


def _primary_metric(result: dict[str, Any]) -> dict[str, Any] | None:
    metrics = result.get("metrics")
    if not isinstance(metrics, dict):
        return None
    primary = metrics.get("primary")
    if isinstance(primary, dict):
        return {
            key: primary.get(key)
            for key in ("name", "direction", "value")
            if primary.get(key) is not None
        }
    if isinstance(primary, (int, float)) and not isinstance(primary, bool) and math.isfinite(float(primary)):
        return {"value": primary}
    for name in ("raw_metric", "score", "nrmse", "lpips", "rmse", "accuracy"):
        value = metrics.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
            return {"name": name, "value": value}
    return None


def _model_files(model_dir: Path) -> list[Path]:
    if not _regular_dir(model_dir):
        return []
    files: list[Path] = []
    try:
        for path in model_dir.rglob("*"):
            if len(files) >= 2_000:
                break
            if _regular_file(path) and path.name not in IGNORED_NAMES:
                files.append(path)
    except OSError:
        pass
    return sorted(files, key=lambda item: item.as_posix().casefold())


def _is_model_payload(path: Path) -> bool:
    if path.name in {"artifact.json", "reload.log", "reload_metrics.json"}:
        return False
    if path.suffix.casefold() in MODEL_EXTENSIONS:
        return True
    return path.suffix.casefold() not in {".json", ".log", ".md", ".txt", ".yaml", ".yml"}


def _classify_extra(
    path: Path,
    submission_root: Path,
    extras: dict[str, list[dict[str, str]]],
    suggestions: list[dict[str, str]],
    category: str | None = None,
    reason: str | None = None,
) -> None:
    rel = _rel(path, submission_root)
    if any(item["path"] == rel for values in extras.values() for item in values):
        return
    if category is None:
        if path.name in MERGE_CANDIDATE_NAMES:
            category, reason = "merge_candidate", MERGE_CANDIDATE_NAMES[path.name]
        elif path.name in MISPLACED_NAMES:
            category, reason = "misplaced", MISPLACED_NAMES[path.name]
        else:
            category, reason = "extra_allowed", "规范外附加材料；多交不构成格式失败。"
    extras[category].append({"path": rel, "reason": reason or ""})
    _suggest(
        suggestions,
        "ORGANIZE_EXTRA" if category != "extra_allowed" else "EXTRA_ALLOWED",
        category,
        rel,
        reason or "规范外附加材料不影响通过；如无审阅价值可精简。",
    )


def _expected_children(
    directory: Path,
    names: set[str],
    submission_root: Path,
    extras: dict[str, list[dict[str, str]]],
    suggestions: list[dict[str, str]],
) -> None:
    if not _regular_dir(directory):
        return
    try:
        entries = sorted(directory.iterdir(), key=lambda item: item.name.casefold())
    except OSError:
        return
    for entry in entries:
        if entry.name in names or entry.name in IGNORED_NAMES:
            continue
        _classify_extra(entry, submission_root, extras, suggestions)


def _require_dir(
    path: Path, submission_root: Path, issues: list[dict[str, str]], code: str, message: str
) -> bool:
    if _regular_dir(path):
        return True
    _issue(issues, code, _rel(path, submission_root), message)
    return False


def _require_file(
    path: Path, submission_root: Path, issues: list[dict[str, str]], code: str, message: str
) -> bool:
    if _regular_file(path):
        return True
    _issue(issues, code, _rel(path, submission_root), message)
    return False


def _collect_run(
    seed_dir: Path,
    expected_role: str,
    submission_root: Path,
    issues: list[dict[str, str]],
    suggestions: list[dict[str, str]],
    extras: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, Any], str | None]:
    label = seed_dir.name[5:]
    signal: str | None = None
    run: dict[str, Any] = {
        "directory": _rel(seed_dir, submission_root),
        "seed_label": label,
        "result": None,
        "run_log": {"present": False, "nonempty": False},
        "model": {"present": False, "files": [], "payloads": [], "artifact": False, "reload": False},
    }
    result_path = seed_dir / "result.json"
    if _require_file(result_path, submission_root, issues, "MISSING_RESULT", "每个 seed 必须包含 result.json。"):
        data, error = _read_json(result_path)
        if error:
            _issue(issues, "INVALID_RESULT_JSON", _rel(result_path, submission_root), error)
        else:
            assert data is not None
            # Presence, rather than truthiness, is intentional: legacy execution
            # metadata may explicitly be null when the gap is documented.
            missing = [key for key in REQUIRED_RESULT_FIELDS if key not in data]
            if missing:
                _issue(
                    issues,
                    "MISSING_RESULT_FIELDS",
                    _rel(result_path, submission_root),
                    "result.json 缺少新版证据字段：" + ", ".join(missing) + "。",
                )
            role = str(data.get("role", "")).casefold()
            if role and role != expected_role:
                _issue(
                    issues,
                    "RESULT_ROLE_MISMATCH",
                    _rel(result_path, submission_root),
                    f"目录角色为 {expected_role}，result.json.role 为 {data.get('role')!r}。",
                )
            actual_seed = _canonical_seed(data.get("seed"))
            expected_seed = _canonical_seed(label)
            if actual_seed is not None and expected_seed is not None and actual_seed != expected_seed:
                _issue(
                    issues,
                    "RESULT_SEED_MISMATCH",
                    _rel(result_path, submission_root),
                    f"目录 seed 为 {label!r}，result.json.seed 为 {data.get('seed')!r}。",
                )
            run["result"] = {
                "valid_json": True,
                "status": data.get("status"),
                "role": data.get("role"),
                "seed": data.get("seed"),
                "task_type": data.get("task_type", data.get("task_kind")),
                "primary_metric": _primary_metric(data),
            }
            signal = _task_signal(data)

    log_path = seed_dir / "run.log"
    present = _require_file(log_path, submission_root, issues, "MISSING_RUN_LOG", "每个 seed 必须包含 run.log。")
    run["run_log"] = {"present": present, "nonempty": _not_empty(log_path)}
    if present and not run["run_log"]["nonempty"]:
        _issue(issues, "EMPTY_RUN_LOG", _rel(log_path, submission_root), "run.log 为空，不能作为运行证据。")

    model_dir = seed_dir / "model"
    model_files = _model_files(model_dir)
    payloads = [path for path in model_files if _is_model_payload(path)]
    artifact_path, reload_path = model_dir / "artifact.json", model_dir / "reload.log"
    run["model"] = {
        "present": _regular_dir(model_dir),
        "files": [_rel(path, submission_root) for path in model_files],
        "payloads": [_rel(path, submission_root) for path in payloads],
        "artifact": _regular_file(artifact_path),
        "reload": _regular_file(reload_path),
    }
    _expected_children(
        seed_dir,
        {"result.json", "run.log", "model"},
        submission_root,
        extras,
        suggestions,
    )
    if _regular_dir(model_dir):
        for path in model_files:
            if path.name in MERGE_CANDIDATE_NAMES:
                _classify_extra(path, submission_root, extras, suggestions)
    return run, signal


def _collect_role(
    role_dir: Path,
    expected_role: str,
    submission_root: Path,
    issues: list[dict[str, str]],
    suggestions: list[dict[str, str]],
    extras: dict[str, list[dict[str, str]]],
) -> tuple[dict[str, Any], list[str]]:
    result: dict[str, Any] = {"path": _rel(role_dir, submission_root), "seeds": [], "runs": {}}
    signals: list[str] = []
    if not _require_dir(
        role_dir,
        submission_root,
        issues,
        "MISSING_ROLE_RUNS",
        f"optimization_evidence 必须包含 {role_dir.name}/。",
    ):
        return result, signals
    try:
        entries = sorted(role_dir.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        _issue(issues, "UNREADABLE_ROLE_RUNS", _rel(role_dir, submission_root), str(exc))
        return result, signals
    for entry in entries:
        if entry.name in IGNORED_NAMES:
            continue
        if _regular_dir(entry) and re.fullmatch(r"seed_.+", entry.name):
            seed = entry.name[5:]
            result["seeds"].append(seed)
            run, signal = _collect_run(
                entry, expected_role, submission_root, issues, suggestions, extras
            )
            result["runs"][seed] = run
            if signal:
                signals.append(signal)
        else:
            _classify_extra(entry, submission_root, extras, suggestions)
    result["seeds"].sort(key=lambda value: (_canonical_seed(value) or value).casefold())
    if not result["seeds"]:
        _issue(
            issues,
            "NO_SEED_RUNS",
            _rel(role_dir, submission_root),
            f"{role_dir.name}/ 中没有 seed_<真实seed>/ 运行目录。",
        )
    return result, signals


def _instruction_task_signal(submission_root: Path) -> str | None:
    text = _read_text(submission_root / "workspace" / "harbor_task" / "instruction.md").casefold()
    if not text:
        return None
    if re.search(r"\b(?:non[- ]training|no training|does not train|without training)\b|非训练型|无需训练|不训练模型", text):
        return "non_training"
    if re.search(r"\b(?:train(?:ing|ed)? from (?:scratch|initialization)|fine[- ]?tun(?:e|ing))\b|从初始化训练|训练模型|模型训练|微调", text):
        return "training"
    return None


def _validate_models(
    roles: list[dict[str, Any]],
    task_kind: str,
    submission_root: Path,
    issues: list[dict[str, str]],
    suggestions: list[dict[str, str]],
    extras: dict[str, list[dict[str, str]]],
) -> None:
    for role in roles:
        for run in role["runs"].values():
            model = run["model"]
            model_path = Path(run["directory"]) / "model"
            if task_kind == "training":
                if not model["present"]:
                    _issue(issues, "MISSING_MODEL_DIR", model_path.as_posix(), "训练型任务每个 seed 必须保留 model/。")
                    continue
                if not model["payloads"]:
                    _issue(issues, "MISSING_MODEL_ARTIFACT", model_path.as_posix(), "model/ 中没有可识别的模型或 checkpoint 产物。")
                artifact_path = model_path / "artifact.json"
                if not model["artifact"]:
                    _issue(issues, "MISSING_ARTIFACT_MANIFEST", artifact_path.as_posix(), "训练型任务需要 model/artifact.json。")
                else:
                    _, error = _read_json(submission_root / artifact_path)
                    if error:
                        _issue(issues, "INVALID_ARTIFACT_JSON", artifact_path.as_posix(), error)
                reload_path = model_path / "reload.log"
                if not model["reload"]:
                    _issue(issues, "MISSING_RELOAD_LOG", reload_path.as_posix(), "训练型任务需要 checkpoint 重载证据 model/reload.log。")
                elif not _not_empty(submission_root / reload_path):
                    _issue(issues, "EMPTY_RELOAD_LOG", reload_path.as_posix(), "model/reload.log 为空。")
            elif task_kind == "non_training" and model["present"]:
                if model["files"]:
                    _classify_extra(
                        submission_root / model_path,
                        submission_root,
                        extras,
                        suggestions,
                        "extra_allowed",
                        "任务声明为非训练型；附带 model/ 不判失败，但建议确认是否确有保留必要。",
                    )
                    _suggest(
                        suggestions,
                        "NON_TRAINING_MODEL_PRESENT",
                        "extra_allowed",
                        model_path.as_posix(),
                        "非训练型任务通常不提交 model/；如无必要可删除。",
                    )
                else:
                    _classify_extra(
                        submission_root / model_path,
                        submission_root,
                        extras,
                        suggestions,
                        "extra_allowed",
                        "非训练型任务的空 model/ 不判失败。",
                    )
                    _suggest(
                        suggestions,
                        "EMPTY_NON_TRAINING_MODEL_DIR",
                        "extra_allowed",
                        model_path.as_posix(),
                        "非训练型任务无需建立空 model/，建议移除空目录。",
                    )


def _comparison_summary(
    path: Path, submission_root: Path, issues: list[dict[str, str]]
) -> dict[str, Any]:
    output: dict[str, Any] = {"path": _rel(path, submission_root), "present": False, "valid_json": False}
    if not _require_file(
        path,
        submission_root,
        issues,
        "MISSING_COMPARISON_SUMMARY",
        "optimization_evidence 必须包含 comparison_summary.json。",
    ):
        return output
    output["present"] = True
    data, error = _read_json(path)
    if error:
        _issue(issues, "INVALID_COMPARISON_SUMMARY", _rel(path, submission_root), error)
        output["error"] = error
        return output
    assert data is not None
    output["valid_json"] = True
    output["status"] = data.get("status")
    metric = data.get("metric")
    if isinstance(metric, dict):
        output["metric"] = {key: metric.get(key) for key in ("name", "direction") if metric.get(key) is not None}
    seeds = data.get("seeds")
    if isinstance(seeds, list):
        output["seeds"] = [value for value in (_canonical_seed(seed) for seed in seeds) if value is not None]
    statistics = data.get("statistics")
    if isinstance(statistics, dict):
        output["statistics"] = {
            key: statistics.get(key)
            for key in (
                "paired_run_count", "baseline_mean", "baseline_sample_std",
                "reference_mean", "reference_sample_std", "mean_paired_improvement",
                "relative_improvement", "baseline_sigma_multiple",
            )
            if statistics.get(key) is not None
        }
    normalized = data.get("normalized_score")
    if isinstance(normalized, dict):
        output["normalized_score"] = {
            key: normalized.get(key)
            for key in ("baseline", "reference", "formula")
            if normalized.get(key) is not None
        }
    significance = data.get("significance_rule")
    if isinstance(significance, dict):
        output["significance_rule"] = {
            key: significance.get(key)
            for key in ("required_baseline_sigma_multiple", "passed")
            if significance.get(key) is not None
        }
    return output


def _collect_trajectory(
    path: Path, submission_root: Path, issues: list[dict[str, str]]
) -> dict[str, Any]:
    """Validate the documented eight-field rounds without inventing runtime evidence."""
    rel = _rel(path, submission_root)
    output: dict[str, Any] = {
        "path": rel, "present": False, "valid_json": False,
        "round_count": 0, "format_valid": False, "status": "missing",
    }
    if not _require_file(
        path, submission_root, issues, "MISSING_TRAJECTORY",
        "v0.3.2 契约需提交两份真实轨迹；允许 review 选择等价文件名。",
    ):
        return output
    output["present"] = True
    data, error = _read_json(path, strict=True)
    if error:
        unreadable = "inspection limit" in error or error.startswith((
            "OSError:", "PermissionError:", "FileNotFoundError:", "UnicodeDecodeError:",
        ))
        _issue(issues, "TRAJECTORY_UNREADABLE" if unreadable else "INVALID_TRAJECTORY_JSON", rel, error)
        output["status"] = "unreadable" if unreadable else "invalid"
        return output
    assert data is not None
    output["valid_json"] = True
    rounds = data.get("rounds")
    if not isinstance(rounds, list):
        _issue(issues, "INVALID_TRAJECTORY_ROUNDS", rel, "最终轨迹 JSON 必须包含 rounds 数组。")
        output["status"] = "invalid"
        return output
    output["round_count"] = len(rounds)
    if not rounds:
        _issue(issues, "EMPTY_TRAJECTORY_ROUNDS", rel, "rounds 为空，长程轨迹尚未完成或缺少证据；不能用虚构轮次补齐。")
        output["status"] = "incomplete"
        return output
    initial_issue_count = len(issues)
    previous_round: int | None = None
    for index, row in enumerate(rounds):
        label = f"rounds[{index}]"
        if not isinstance(row, dict):
            _issue(issues, "INVALID_TRAJECTORY_ROUND", rel, f"{label} 必须为 JSON 对象。")
            continue
        missing = [field for field in REQUIRED_TRAJECTORY_FIELDS if field not in row]
        if missing:
            _issue(issues, "MISSING_TRAJECTORY_FIELDS", rel, f"{label} 缺少字段：{', '.join(missing)}。")

        def invalid(field: str, message: str) -> None:
            _issue(issues, "INVALID_TRAJECTORY_FIELD", rel, f"{label}.{field} {message}")

        number = row.get("round")
        if "round" in row:
            if isinstance(number, bool) or not isinstance(number, int) or number < 1:
                invalid("round", "必须为正整数。")
            else:
                if previous_round is not None and number <= previous_round:
                    _issue(issues, "TRAJECTORY_ROUND_ORDER", rel, f"{label}.round 必须按轮次递增，不得重复或倒序。")
                previous_round = number
        for field in ("policy_name", "method_summary", "status"):
            if field in row and (not isinstance(row[field], str) or not row[field].strip()):
                invalid(field, "必须为非空字符串。")
        status = row.get("status")
        valid_status = isinstance(status, str) and bool(status.strip())
        normalized_status = status.strip().casefold() if valid_status else None
        successful = normalized_status in SUCCESS_STATUSES
        failed = normalized_status in FAILURE_STATUSES
        if valid_status and not successful and not failed:
            _issue(issues, "TRAJECTORY_STATUS_REVIEW", rel, f"{label}.status={status!r} 为自定义状态；需人工确认成功/失败含义及对应分数、失败原因和最佳标记。")
        if "score" in row:
            score = row["score"]
            finite_score = (
                isinstance(score, (int, float)) and not isinstance(score, bool)
                and (isinstance(score, int) or math.isfinite(score))
            )
            if not finite_score and not (score is None and valid_status and not successful):
                invalid("score", "成功时必须为有限数值；失败时可为 null 或实际有限指标。")
        if "failure_reason" in row and valid_status:
            reason = row["failure_reason"]
            if successful and reason is not None:
                invalid("failure_reason", "成功（status=ok）时应为 null。")
            elif failed and (not isinstance(reason, str) or not reason.strip()):
                invalid("failure_reason", "失败时必须说明真实失败原因。")
            elif not successful and not failed and reason is not None and not isinstance(reason, str):
                invalid("failure_reason", "必须为字符串或 null。")
        if "retained_best" in row:
            if not isinstance(row["retained_best"], bool):
                invalid("retained_best", "必须为布尔值。")
            elif failed and row["retained_best"]:
                invalid("retained_best", "失败轮次不能被标记为最佳有效方法。")
        if "time" in row:
            value = row["time"]
            try:
                if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}", value):
                    raise ValueError("expected timestamp")
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                invalid("time", "必须为可解析的完成时间，例如 YYYY-MM-DD HH:mm:ss 或 ISO 8601 时间。")
    own_issues = issues[initial_issue_count:]
    hard_issues = [item for item in own_issues if item["code"] != "TRAJECTORY_STATUS_REVIEW"]
    output["format_valid"] = not own_issues
    output["status"] = "invalid" if hard_issues else "manual" if own_issues else "valid"
    return output


def validate_trajectory(path: str | Path, submission_root: str | Path | None = None) -> dict[str, Any]:
    """Validate one evidence path selected by the review, including equivalent names."""
    selected = Path(path).expanduser()
    root = Path(submission_root) if submission_root is not None else selected.parent
    issues: list[dict[str, str]] = []
    output = _collect_trajectory(selected, root, issues)
    output["alignment_issues"] = issues
    return output


def collect(root: str | Path) -> dict[str, Any]:
    """Collect simplified-format observations without mutating or executing the package."""
    inspected_root = Path(root).expanduser().resolve()
    issues: list[dict[str, str]] = []
    suggestions: list[dict[str, str]] = []
    extras: dict[str, list[dict[str, str]]] = {
        "extra_allowed": [], "merge_candidate": [], "misplaced": []
    }
    if not _regular_dir(inspected_root):
        _issue(issues, "INVALID_INPUT", inspected_root.name or ".", "输入必须是已安全解压的普通目录。")
        missing = [row for row in issues if row["code"].startswith("MISSING_")]
        return {
            "schema_version": SCHEMA_VERSION,
            "inspected_root": ".",
            "submission_root": None,
            "wrapper_depth": None,
            "status": "manual",
            "summary": "输入目录不存在或不是可读的普通目录。",
            "missing": missing,
            "issues": [row for row in issues if row not in missing],
            "alignment_issues": issues,
            "suggestions": suggestions,
            "extras": extras,
            "optimization_evidence": {"present": False},
        }

    submission_root, alternatives = _locate_submission_root(inspected_root)
    try:
        wrapper_depth = len(submission_root.relative_to(inspected_root).parts)
    except ValueError:
        wrapper_depth = 0
    if alternatives:
        _issue(
            issues,
            "AMBIGUOUS_SUBMISSION_ROOT",
            _rel(submission_root, inspected_root),
            "发现多个同等候选提交根目录：" + ", ".join(alternatives) + "。",
        )

    top_present = [name for name in REQUIRED_TOP_LEVEL if _regular_dir(submission_root / name)]
    top_missing = [name for name in REQUIRED_TOP_LEVEL if name not in top_present]
    for name in top_missing:
        _issue(
            issues,
            "MISSING_TOP_LEVEL",
            name,
            f"提交根目录缺少必需顶层目录 {name}/。",
        )
    try:
        top_entries = sorted(submission_root.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        top_entries = []
        _issue(issues, "UNREADABLE_SUBMISSION_ROOT", ".", str(exc))
    for entry in top_entries:
        if entry.name in REQUIRED_TOP_LEVEL or entry.name in IGNORED_NAMES:
            continue
        _classify_extra(entry, submission_root, extras, suggestions)

    workspace = submission_root / "workspace"
    harbor = workspace / "harbor_task"
    reference = workspace / "reference"
    if _regular_dir(workspace):
        _require_dir(harbor, submission_root, issues, "MISSING_HARBOR_TASK", "workspace/ 必须包含 harbor_task/。")
        _require_dir(reference, submission_root, issues, "MISSING_REFERENCE", "workspace/ 必须包含 reference/。")
        _expected_children(workspace, {"harbor_task", "reference"}, submission_root, extras, suggestions)
    if _regular_dir(harbor):
        for name in REQUIRED_HARBOR_FILES:
            _require_file(harbor / name, submission_root, issues, "MISSING_HARBOR_FILE", f"harbor_task 缺少 {name}。")
        for name in REQUIRED_HARBOR_DIRS:
            _require_dir(harbor / name, submission_root, issues, "MISSING_HARBOR_DIR", f"harbor_task 缺少 {name}/。")
        for path in (harbor / "environment" / "Dockerfile", harbor / "tests" / "Dockerfile"):
            _require_file(path, submission_root, issues, "MISSING_HARBOR_DOCKERFILE", f"harbor_task 缺少 {path.parent.name}/Dockerfile。")
        # Hidden evaluation material is mandatory, but its directory name and
        # delivery method are task-specific. harbor_review validates the material,
        # generation/injection evidence and isolation; format alone cannot do so.
        _expected_children(
            harbor,
            set(REQUIRED_HARBOR_FILES) | set(REQUIRED_HARBOR_DIRS) | set(OPTIONAL_HARBOR_DIRS),
            submission_root,
            extras,
            suggestions,
        )
        for legacy in (harbor / "environment" / "trusted", harbor / "tests" / "trusted"):
            if _regular_dir(legacy):
                _classify_extra(legacy, submission_root, extras, suggestions)
                _suggest(
                    suggestions,
                    "LEGACY_TRUSTED_DIR",
                    "obsolete_layout",
                    _rel(legacy, submission_root),
                    "新版格式不再设置 trusted/；建议将评分侧实现平铺整合进 tests/。",
                )
        runtime = harbor / "tests" / "runtime"
        if _regular_dir(runtime):
            _classify_extra(runtime, submission_root, extras, suggestions)
            _suggest(
                suggestions,
                "LEGACY_TESTS_RUNTIME",
                "obsolete_layout",
                _rel(runtime, submission_root),
                "新版格式不再设置 tests/runtime/；建议将必要评分文件平铺进 tests/。",
            )

    expert = submission_root / "expert_evidence"
    trajectory_paths = sorted(expert.glob("trajectory*.json")) if _regular_dir(expert) else []
    expert_evidence = {
        "present": _regular_dir(expert),
        "trajectories": {
            path.name: _collect_trajectory(path, submission_root, issues)
            for path in trajectory_paths
        },
        "completeness_note": "两条模型轨迹是否齐全按 overview 中的 source_path 核验；允许等价文件名，不以目录扫描代替完整性审查。",
    }
    opt = submission_root / "optimization_evidence"
    optimization: dict[str, Any] = {
        "present": _regular_dir(opt),
        "path": "optimization_evidence",
        "task_kind": "unknown",
        "baseline_runs": {"path": "optimization_evidence/baseline_runs", "seeds": [], "runs": {}},
        "reference_runs": {"path": "optimization_evidence/reference_runs", "seeds": [], "runs": {}},
        "seed_pairing": {"paired": False, "baseline_only": [], "reference_only": [], "paired_seeds": []},
        "comparison_summary": {"path": "optimization_evidence/comparison_summary.json", "present": False, "valid_json": False},
    }
    if _regular_dir(opt):
        _require_file(
            opt / "训练证据说明.md",
            submission_root,
            issues,
            "MISSING_TRAINING_EVIDENCE_NOTE",
            "optimization_evidence 缺少中文训练证据说明.md。",
        )
        baseline, baseline_signals = _collect_role(
            opt / "baseline_runs", "baseline", submission_root, issues, suggestions, extras
        )
        reference_runs, reference_signals = _collect_role(
            opt / "reference_runs", "reference", submission_root, issues, suggestions, extras
        )
        optimization["baseline_runs"] = baseline
        optimization["reference_runs"] = reference_runs
        baseline_set, reference_set = set(baseline["seeds"]), set(reference_runs["seeds"])
        baseline_only = sorted(baseline_set - reference_set)
        reference_only = sorted(reference_set - baseline_set)
        paired = sorted(baseline_set & reference_set)
        optimization["seed_pairing"] = {
            "paired": bool(paired) and not baseline_only and not reference_only,
            "baseline_only": baseline_only,
            "reference_only": reference_only,
            "paired_seeds": paired,
        }
        if baseline_only or reference_only:
            _issue(
                issues,
                "UNPAIRED_SEEDS",
                "optimization_evidence",
                f"Baseline/Reference seed 集合不一致；仅 Baseline: {baseline_only or '无'}；仅 Reference: {reference_only or '无'}。",
            )
        optimization["comparison_summary"] = _comparison_summary(
            opt / "comparison_summary.json", submission_root, issues
        )
        summary_seeds = optimization["comparison_summary"].get("seeds")
        if summary_seeds is not None and set(summary_seeds) != set(paired):
            _issue(
                issues,
                "SUMMARY_SEED_MISMATCH",
                "optimization_evidence/comparison_summary.json",
                "comparison_summary.json 的 seeds 与逐轮成对 seed 集不一致。",
            )

        signals = baseline_signals + reference_signals
        explicit = set(signals)
        if len(explicit) > 1:
            _issue(
                issues,
                "INCONSISTENT_TASK_KIND",
                "optimization_evidence",
                "不同 result.json 对训练型/非训练型任务的声明不一致。",
            )
        if "training" in explicit:
            task_kind = "training"
        elif "non_training" in explicit:
            task_kind = "non_training"
        else:
            task_kind = _instruction_task_signal(submission_root) or "unknown"
        optimization["task_kind"] = task_kind
        _validate_models(
            [baseline, reference_runs], task_kind, submission_root, issues, suggestions, extras
        )

        expected_opt = {
            "训练证据说明.md", "baseline_runs", "reference_runs", "comparison_summary.json"
        }
        _expected_children(opt, expected_opt, submission_root, extras, suggestions)
    else:
        # The missing top-level issue already explains the failure; keep the nested
        # summary stable for report rendering.
        optimization["present"] = False

    for values in extras.values():
        values.sort(key=lambda item: item["path"].casefold())
    suggestions.sort(key=lambda item: (item["path"].casefold(), item["code"]))
    issues.sort(key=lambda item: (item["path"].casefold(), item["code"]))

    missing = sorted(
        (
            row
            for row in issues
            if row["code"].startswith("MISSING_") or row["code"] == "NO_SEED_RUNS"
        ),
        key=lambda row: (row["path"].casefold(), row["code"]),
    )
    other_issues = [row for row in issues if row not in missing]
    extra_count = sum(len(values) for values in extras.values())
    status = "deviations" if issues else "aligned_with_extras" if extra_count else "aligned"
    if status == "aligned":
        summary = "规范结构已对齐，未发现额外材料。"
    elif status == "aligned_with_extras":
        summary = f"规范结构已对齐；另记录 {len(suggestions)} 条非阻断整理建议。"
    else:
        summary = f"发现 {len(issues)} 个格式对齐问题；另记录 {len(suggestions)} 条非阻断整理建议。"
    return {
        "schema_version": SCHEMA_VERSION,
        "inspected_root": ".",
        "submission_root": _rel(submission_root, inspected_root),
        "submission_root_relative": _rel(submission_root, inspected_root),
        "wrapper_depth": wrapper_depth,
        "status": status,
        "summary": summary,
        "missing": missing,
        "issues": other_issues,
        "alignment_issues": issues,
        "suggestions": suggestions,
        "top_level": {
            "required": list(REQUIRED_TOP_LEVEL),
            "present": top_present,
            "missing": top_missing,
        },
        "harbor_task": {
            "path": "workspace/harbor_task",
            "present": _regular_dir(harbor),
            "reference_present": _regular_dir(reference),
            "legacy_trusted_present": any(
                _regular_dir(path)
                for path in (harbor / "environment" / "trusted", harbor / "tests" / "trusted")
            ),
            "legacy_tests_runtime_present": _regular_dir(harbor / "tests" / "runtime"),
        },
        "extras": extras,
        "expert_evidence": expert_evidence,
        "optimization_evidence": optimization,
    }


__all__ = ["collect", "validate_trajectory"]
