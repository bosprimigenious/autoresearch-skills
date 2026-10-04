"""Harbor evidence inventory and semantic-review contract, never a task runner.

This module does not import Harbor or package code, build images, or run scripts.
Structural observations are not a substitute for version-specific schema review.
"""
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re

import docker_paths

try:
    import tomllib
except ImportError:
    tomllib = None

LIMIT = 4 * 1024 * 1024
RULESET = "harbor-compatibility-v0.3.4"
INTERNAL_SOURCE = "reference:packaged-harbor-evidence"
OFFICIAL_SOURCE = "https://docs.harborframework.com/core-concepts/tasks/separate-verifier"
H_IDS = [f"H{i:02d}" for i in range(1, 7)]
H_TITLES = ["任务目录", "版本与配置", "环境与运行路径", "测试入口与 reward", "Job/Trial 调用配置", "NOP 自检记录"]


def bounded_text(path):
    if path.stat().st_size > LIMIT:
        raise ValueError("file exceeds the 4 MiB inspection limit")
    return path.read_text(encoding="utf-8")


def collect(root, files):
    cfg = None
    configs = [p for p in files if Path(p).name == "task.toml" and ".git" not in Path(p).parts]
    configs = sorted(configs, key=lambda p: (len(Path(p).parts), p))
    selected = None
    if configs:
        depth = len(Path(configs[0]).parts)
        nearest = [p for p in configs if len(Path(p).parts) == depth]
        if len(nearest) == 1:
            selected = Path(nearest[0]).parent.as_posix()
    observations = {"task_candidates": [Path(p).parent.as_posix() for p in configs],
                    "task_root": selected, "toml_parser": "tomllib" if tomllib else "unavailable",
                    "config_sections": [], "schema_version": None, "verifier_environment_mode": None,
                    "parse_error": None}
    if selected is not None:
        try:
            raw = bounded_text(root / selected / "task.toml")
            if tomllib:
                cfg = tomllib.loads(raw)
                observations["config_sections"] = list(cfg)
                observations["schema_version"] = cfg.get("schema_version", cfg.get("version"))
                verifier = cfg.get("verifier")
                observations["verifier_environment_mode"] = verifier.get("environment_mode") if isinstance(verifier, dict) else None
            else:
                observations["config_sections"] = re.findall(r"(?m)^\[([^\[\]\n]+)\]", raw)
        except (OSError, ValueError, UnicodeError) as exc:
            observations["parse_error"] = str(exc)
    observations["job_configs"] = [p for p in files if Path(p).name in ("job.yaml", "job.yml", "trial.yaml", "trial.yml")]
    observations["trial_results"] = [p for p in files if Path(p).name == "result.json"]
    return {"ruleset": RULESET, "sources": [INTERNAL_SOURCE, OFFICIAL_SOURCE],
            "target_version": None, "provider": None, "observations": observations,
            "path_contract": docker_paths.inspect(root, selected, cfg, require_separate=True),
            "checks": [], "static_status": "manual", "runtime_status": "not_run"}


def resolve_ref(root, ref):
    if not isinstance(ref, str):
        raise ValueError("Harbor evidence must be a string")
    name = re.sub(r":\d+$", "", ref.split("#", 1)[0])
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Harbor evidence is not a file inside the inspected bundle: {ref}")
    return path


def numeric_reward(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def read_reward(path):
    raw = bounded_text(path)
    if path.name == "reward.txt":
        try:
            value = float(raw.strip())
        except ValueError as exc:
            raise ValueError("reward.txt must contain a single numeric value") from exc
        reward = {"reward": value}
    else:
        reward = json.loads(raw)
    if not isinstance(reward, dict) or not reward or not all(numeric_reward(v) for v in reward.values()):
        raise ValueError("reward must be a nonempty numeric map with finite values, not status strings/bools")
    return reward


def trial_task_identity(root, task_root, config, result, binding=None):
    """Names locate a task; cited human review links its version. Never authenticates logs."""
    if task_root is None:
        return {"status": "manual", "basis": "task_root_unknown"}
    task = config.get("task") if isinstance(config.get("task"), dict) else {}
    cfg = {}
    if tomllib:
        cfg = tomllib.loads(bounded_text(task_root / "task.toml"))
    metadata = cfg.get("metadata") if isinstance(cfg.get("metadata"), dict) else {}
    names = {task_root.name}
    for name in (cfg.get("name"), metadata.get("name")):
        if isinstance(name, str) and name.strip():
            names.add(name)
    identities = []
    for value in (task.get("name"), result.get("task_name")):
        if isinstance(value, str) and value.strip():
            identities.append(value.strip())
    raw_path = task.get("path")
    if isinstance(raw_path, str) and raw_path.strip() and raw_path not in (".", "/"):
        identities.append(PurePosixPath(raw_path.replace("\\", "/")).name)
    mismatches = [name for name in identities if name not in names]
    digest = hashlib.sha256((task_root / "task.toml").read_bytes()).hexdigest()
    for value in (task.get("task_toml_sha256"), result.get("task_toml_sha256")):
        if value is not None and value != digest:
            raise ValueError("Harbor Trial task.toml hash disagrees with the inspected task")
    if binding is None:
        if mismatches:
            raise ValueError("Harbor Trial identifies a different task; a reviewed task mapping is required")
        return {"status": "manual", "basis": "name_only" if identities else "identity_missing"}
    if not isinstance(binding, dict) or not isinstance(binding.get("summary"), str) or not binding["summary"].strip():
        raise ValueError("trial_task_binding requires a semantic version-mapping summary")
    binding_refs = binding.get("evidence")
    if not isinstance(binding_refs, list) or not binding_refs:
        raise ValueError("trial_task_binding requires real package evidence")
    paths = [resolve_ref(root, ref) for ref in binding_refs]
    for path in paths:
        if not bounded_text(path).strip():
            raise ValueError("trial_task_binding evidence cannot be empty")
    if mismatches:
        # Renaming must be explicit in the cited mapping, not inferred from arbitrary logs.
        # JSON/text provenance records are reviewed by the human; this only prevents a
        # weak reference to an unrelated file from hiding contradictory task names.
        mapping_text = "\n".join(bounded_text(path) for path in paths
                                  if path.name not in ("config.json", "result.json", "task.toml", "trial.log", "test-stdout.txt"))
        if not mapping_text or not all(name in mapping_text for name in mismatches) or not any(name in mapping_text for name in names):
            raise ValueError("different-task evidence needs an explicit cited mapping of old and current task names")
    return {"status": "pass", "basis": "reviewed_evidence", "evidence": binding_refs,
            "summary": binding["summary"], "authenticity_verified": False}


def validate_trial_evidence(root, refs, task_root=None, binding=None):
    """Cross-check one NOP or candidate Trial, with separate identity/version review."""
    paths = [resolve_ref(root, ref) for ref in refs]
    results = [p for p in paths if p.name == "result.json"]
    for result_path in results:
        trial = result_path.parent
        config = trial / "config.json"
        json_reward = trial / "verifier" / "reward.json"
        text_reward = trial / "verifier" / "reward.txt"
        reward_path = json_reward if json_reward.is_file() else text_reward
        logs = [p for p in paths if p in (trial / "trial.log", trial / "verifier" / "test-stdout.txt")]
        if config not in paths or reward_path not in paths or not logs:
            continue
        result = json.loads(bounded_text(result_path))
        config_value = json.loads(bounded_text(config))
        if not isinstance(config_value, dict) or not config_value or not isinstance(result, dict):
            raise ValueError("Harbor trial config/result must be nonempty objects")
        agent_config = config_value.get("agent")
        agent_info = result.get("agent_info")
        if not isinstance(agent_config, dict) or not isinstance(agent_config.get("name"), str) or not agent_config["name"].strip():
            raise ValueError("H06 requires config.json agent.name")
        if not isinstance(agent_info, dict) or agent_info.get("name") != agent_config["name"]:
            raise ValueError("H06 requires consistent config/result Agent names")
        if result.get("verifier_environment_mode") != "separate":
            raise ValueError("H06 requires result.json verifier_environment_mode = separate")
        if result.get("exception_info"):
            raise ValueError("Harbor trial has exception_info; cannot claim successful run evidence")
        if not result.get("finished_at"):
            raise ValueError("Harbor trial is missing finished_at")
        verifier = result.get("verifier_result")
        rewards = verifier.get("rewards") if isinstance(verifier, dict) else None
        expected = read_reward(reward_path)
        if not isinstance(rewards, dict) or not rewards or not all(numeric_reward(v) for v in rewards.values()):
            raise ValueError("Harbor result.verifier_result.rewards must contain finite numeric rewards")
        if set(rewards) != set(expected) or any(not math.isclose(rewards[k], v, rel_tol=1e-9, abs_tol=1e-12) for k, v in expected.items()):
            raise ValueError("Harbor result rewards disagree with the selected reward file")
        if not any(bounded_text(log).strip() for log in logs):
            raise ValueError("Harbor trial log evidence is empty")
        identity = trial_task_identity(root, task_root, config_value, result, binding)
        return {"trial_dir": trial.relative_to(root.resolve()).as_posix(), "agent": agent_config["name"],
                "verifier_environment_mode": "separate", "rewards": expected, "task_binding": identity}
    raise ValueError("H06 pass requires config.json, result.json, verifier/reward.*, and trial.log or verifier/test-stdout.txt from the same trial")


def aggregate(rows):
    statuses = {row["status"] for row in rows}
    return "fail" if "fail" in statuses else "manual" if "manual" in statuses else "pass"


def hidden_contract(root, declaration):
    """Validate references for an explicit semantic review, not a directory convention."""
    if declaration is None:
        return {"status": "manual", "summary": "Hidden 评测材料必需；尚未复核供给方式、实际材料及评分调用关系。"}
    if not isinstance(declaration, dict) or declaration.get("mode") not in ("prebuilt", "generated", "injected"):
        raise ValueError("hidden_review.mode must be prebuilt, generated, or injected")
    if not isinstance(declaration.get("summary"), str) or not declaration["summary"].strip():
        raise ValueError("hidden_review requires a specific material-supply and grading-use summary")
    refs = declaration.get("evidence")
    if not isinstance(refs, list) or not refs:
        raise ValueError("hidden_review requires real source/configuration evidence")
    for ref in refs:
        resolve_ref(root, ref)
    assets = declaration.get("asset_paths", [])
    if not isinstance(assets, list):
        raise ValueError("hidden_review.asset_paths must be an array")
    if declaration["mode"] == "prebuilt" and not assets:
        return {"status": "manual", "summary": "预置 Hidden 需要引用实际数据文件；目录名或 README 不能替代材料。"}
    for ref in assets:
        path = resolve_ref(root, ref)
        if not path.stat().st_size or path.name.lower() in (".gitkeep", ".keep", ".ds_store", "readme.md", "readme.txt"):
            raise ValueError("hidden_review asset_paths must reference nonempty actual data, not placeholders")
    return {**declaration, "status": "pass", "basis": "reviewed_evidence",
            "note": "仅核验声明与文件引用；材料用途、隔离和生成/注入可用性由语义复核负责。"}


def artifact_contract(root, cfg, review):
    requested = review.get("submission_paths", ["/workspace/solution"])
    if not isinstance(requested, list) or not requested or any(not isinstance(p, str) or not p.startswith("/") or ".." in PurePosixPath(p).parts for p in requested):
        raise ValueError("submission_paths must list absolute Verifier input paths")
    override = review.get("artifact_override")
    if override is not None:
        if not isinstance(override, dict) or not isinstance(override.get("summary"), str) or not override["summary"].strip() or not isinstance(override.get("evidence"), list) or not override["evidence"]:
            raise ValueError("artifact_override requires a target-version Job override summary and real evidence")
        for ref in override["evidence"]:
            resolve_ref(root, ref)
        return {"status": "manual", "submission_paths": requested, "override": override,
                "summary": "存在 Job/版本级 artifacts 覆盖声明；需按目标版本人工核对有效配置与移交路径。"}
    if not isinstance(cfg, dict):
        return {"status": "manual", "submission_paths": requested, "summary": "未获得可解析配置，不能确认 artifacts 移交。"}
    artifacts = cfg.get("artifacts", [])
    if not isinstance(artifacts, list):
        return {"status": "fail", "submission_paths": requested, "summary": "task.toml 顶层 artifacts 必须是列表；默认移交目录不能抵消无效配置。"}
    # Harbor transfers /logs/artifacts to a separate Verifier by default. Extra
    # paths need explicit artifacts; an empty list does not disable this default.
    sources, ambiguous = [PurePosixPath("/logs/artifacts")], False
    for entry in artifacts:
        source = entry.get("source") if isinstance(entry, dict) else entry
        if not isinstance(source, str) or not source.startswith("/") or ".." in PurePosixPath(source).parts:
            return {"status": "fail", "submission_paths": requested, "summary": "artifacts 条目需是绝对源路径或含绝对 source 的对象。"}
        if any(c in source for c in "*?[") or (isinstance(entry, dict) and entry.get("service", "main") != "main"):
            ambiguous = True
        else:
            sources.append(PurePosixPath(source))
    uncovered = [p for p in requested if not any(PurePosixPath(p) == s or s in PurePosixPath(p).parents for s in sources)]
    status = "pass" if not uncovered else "manual" if ambiguous else "fail"
    return {"status": status, "submission_paths": requested, "sources": [str(p) for p in sources],
            "default_sources": ["/logs/artifacts"], "uncovered": uncovered,
            "ambiguous_extra_sources": ambiguous,
            "summary": "尚有未覆盖读取路径，且 artifacts 包含静态无法确定的 glob/service；需人工复核。" if uncovered and ambiguous else
                       "artifacts 未覆盖评分读取路径：" + "、".join(uncovered) if uncovered else
                       "读取路径已由默认 /logs/artifacts 或显式源路径覆盖；运行移交另看 Trial 证据。"}


def apply_review(current, review, root):
    if not isinstance(review, dict):
        raise ValueError("review.harbor must include the six Harbor compatibility conclusions")
    rows = review.get("checks")
    if not isinstance(rows, list) or len(rows) != 6 or not all(isinstance(r, dict) for r in rows) or {r.get("id") for r in rows} != set(H_IDS):
        raise ValueError("Harbor review must contain H01–H06 exactly once")
    for field in ("target_version", "provider", "version_basis"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise ValueError(f"Harbor review requires {field}; use unknown and manual when it cannot be established")
    selected = review.get("task_root", current["observations"].get("task_root"))
    task_root = (root / selected).resolve() if isinstance(selected, str) else None
    if task_root and not task_root.is_relative_to(root.resolve()):
        raise ValueError("Harbor task_root cannot escape the inspected bundle")
    declaration = review.get("path_contract")
    if not isinstance(declaration, dict) or declaration.get("profile") not in docker_paths.PROFILES:
        raise ValueError("Harbor path_contract requires an explicit supported profile")
    if not isinstance(declaration.get("profile_basis"), str) or not declaration["profile_basis"].strip():
        raise ValueError("Harbor path_contract requires profile_basis")
    adapter_refs = declaration.get("adapter_evidence", [])
    if not isinstance(adapter_refs, list):
        raise ValueError("Harbor path_contract.adapter_evidence must be an array")
    for ref in adapter_refs:
        resolve_ref(root, ref)
    cfg = None
    if task_root and (task_root / "task.toml").is_file() and tomllib:
        try:
            cfg = tomllib.loads(bounded_text(task_root / "task.toml"))
        except (OSError, ValueError, UnicodeError):
            pass  # H02 owns TOML syntax; never infer config fields from malformed text.
    path_contract = docker_paths.inspect(root, selected, cfg, declaration, require_separate=True)
    hidden = hidden_contract(root, review.get("hidden_review"))
    artifacts = artifact_contract(root, cfg, review)
    trial_evidence = None
    manual_resolution = declaration.get("manual_resolution")
    if manual_resolution is not None:
        if not isinstance(manual_resolution, dict):
            raise ValueError("path_contract.manual_resolution must be an object")
        reason, refs = manual_resolution.get("summary"), manual_resolution.get("evidence")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500 or not isinstance(refs, list) or not refs:
            raise ValueError("path_contract.manual_resolution requires concise summary and real evidence")
        for ref in refs:
            resolve_ref(root, ref)
        path_contract["manual_resolution"] = manual_resolution
    path_contract["adapter_evidence"] = adapter_refs
    final = []
    for row in sorted(rows, key=lambda r: r["id"]):
        check_id, status = row["id"], row.get("status")
        if status not in ("pass", "fail", "manual", "not_applicable"):
            raise ValueError(f"{check_id}: invalid Harbor status")
        if status == "not_applicable" and check_id not in ("H05", "H06"):
            raise ValueError(f"{check_id}: required Harbor check cannot be skipped")
        reason, refs = row.get("summary"), row.get("evidence", [])
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 220 or not isinstance(refs, list):
            raise ValueError(f"{check_id}: concise summary and evidence array required")
        paths = [resolve_ref(root, ref) for ref in refs]
        if check_id == "H06" and not refs:
            status = "fail"
            reason = "缺少必交的当前题包版本 NOP 自检记录；请提交一次成功运行的记录，确认题包能构建并跑通独立 Verifier。"
        if status == "pass" and not refs:
            raise ValueError(f"{check_id}: passing requires evidence")
        if check_id in ("H01", "H02") and status == "pass":
            if not task_root or not (task_root / "task.toml").is_file():
                raise ValueError(f"{check_id}: no task.toml at the selected task root")
            if task_root / "task.toml" not in paths:
                raise ValueError(f"{check_id}: cite the selected task.toml")
        if check_id == "H02" and status == "pass":
            if review["target_version"] == "unknown" or review["version_basis"] == "unknown":
                raise ValueError("H02: unknown version basis cannot pass")
            if tomllib:
                tomllib.loads(bounded_text(task_root / "task.toml"))
        if check_id == "H01" and status == "pass":
            environment = cfg.get("environment") if isinstance(cfg, dict) else None
            verifier_cfg = cfg.get("verifier") if isinstance(cfg, dict) else None
            verifier_env = verifier_cfg.get("environment") if isinstance(verifier_cfg, dict) else None
            os_name = (verifier_env or environment or {}).get("os") if isinstance(verifier_env or environment or {}, dict) else None
            test_name = "test.bat" if os_name == "windows" else "test.sh"
            required = [task_root / "environment" / "Dockerfile", task_root / "tests" / "Dockerfile",
                        task_root / "tests" / test_name]
            missing = [p.relative_to(task_root).as_posix() for p in required if not p.is_file() or p.is_symlink()]
            if missing:
                status = "fail"
                reason = "任务交付缺少：" + "、".join(missing)
            elif hidden["status"] != "pass":
                status = "manual"
                reason = hidden["summary"]
        if check_id == "H02" and status == "pass":
            verifier = cfg.get("verifier") if isinstance(cfg, dict) else None
            if not isinstance(verifier, dict) or verifier.get("environment_mode") != "separate":
                status = "fail"
                reason = 'task.toml 的 [verifier] 必须显式设置 environment_mode = "separate"。'
        if check_id == "H02" and artifacts["status"] == "fail":
            status, reason = "fail", artifacts["summary"]
        elif check_id == "H02" and status == "pass" and artifacts["status"] == "manual":
            status, reason = "manual", artifacts["summary"]
        if check_id == "H03" and status == "pass" and review["provider"] == "unknown":
            raise ValueError("H03: unknown provider cannot pass")
        if check_id == "H03":
            if path_contract["status"] == "fail":
                status = "fail"
                problems = [f for f in path_contract["findings"] if f["status"] == "fail"]
                reason = ("路径检查不通过：" + "；".join(p["message"] for p in problems))[:220]
                refs = sorted(set(refs + [r for p in problems for r in p["evidence"]]))
            elif path_contract["status"] == "manual" and status == "pass" and (not manual_resolution or (declaration["profile"] == "custom" and not adapter_refs)):
                status = "manual"
                reason = "路径存在静态无法解析项；需提供 path_contract.manual_resolution 的真实证据后才能通过。"
            elif status == "pass" and not {task_root / "environment" / "Dockerfile", task_root / "tests" / "Dockerfile"} <= set(paths):
                status = "manual"
                reason = "H03 需同时引用 Agent 与 Verifier 的 Dockerfile 并复核双镜像构建路径。"
        if check_id == "H06" and status == "pass":
            trial_evidence = validate_trial_evidence(root, refs, task_root, review.get("trial_task_binding"))
            if trial_evidence["agent"] != "nop":
                status = "fail"
                reason = "必交一次 NOP 自检记录；当前提供的是其他 Agent 的 Trial，请补交当前题包版本的 NOP 记录。"
            elif trial_evidence["task_binding"]["status"] != "pass":
                status = "manual"
                reason = "NOP 运行记录一致，当前题包版本尚待确认；可引用已有配置、日志或专家说明完成关联。"
        if check_id == "H06" and status == "not_applicable" and refs:
            raise ValueError("H06: supplied runtime evidence must be reviewed, not skipped")
        final.append({"id": check_id, "title": H_TITLES[H_IDS.index(check_id)], "status": status,
                      "summary": reason, "evidence": refs})
    if final[4]["status"] == "not_applicable" and any(Path(re.sub(r":\d+$", "", ref.split("#", 1)[0])).name == "config.json" for ref in final[5]["evidence"]):
        final[4]["status"] = "manual"
        final[4]["summary"] = "已有 Trial config.json，须复核实际调用配置与任务选择。"
    current = dict(current)
    current.update({k: review[k] for k in ("target_version", "provider", "version_basis")})
    current["task_root"] = selected
    current["path_contract"] = path_contract
    current["hidden_review"] = hidden
    current["artifact_contract"] = artifacts
    current["trial_evidence"] = trial_evidence
    current["checks"] = final
    current["static_status"] = aggregate(final[:5])
    runtime = final[5]["status"]
    current["runtime_status"] = "not_run" if not final[5]["evidence"] else {
        "pass": "evidence_consistent", "fail": "failed", "manual": "unverified", "not_applicable": "not_run"
    }[runtime]
    current["qa17_status"] = aggregate(final)
    return current


def summary(harbor):
    static = {"pass": "通过", "fail": "不通过", "manual": "未完成"}[harbor["static_status"]]
    runtime = {"not_run": "缺少必交 NOP 自检记录，动态运行未验证，QA17 不通过",
               "evidence_consistent": "已有 NOP 自检记录支持当前版本构建与运行可用，未独立复跑或鉴定真伪",
               "failed": "已提交自检记录不满足 NOP 运行要求", "unverified": "已提交 NOP 的运行或任务版本关联待核实"}[harbor["runtime_status"]]
    return f"Harbor 格式/接口：{static}；{runtime}。"
