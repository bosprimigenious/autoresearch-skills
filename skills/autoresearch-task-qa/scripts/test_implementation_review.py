import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

import implementation_review as qa


class ImplementationReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "artifact"
        self.root.mkdir()
        (self.root / "instruction.md").write_text("Hidden data is private. Do not access reference solutions.")
        (self.root / "task.toml").write_text('artifacts = ["/workspace/solution"]\n[metadata]\nname = "fixture"\n[verifier]\nenvironment_mode = "separate"\n')
        (self.root / "environment").mkdir()
        (self.root / "environment/Dockerfile").write_text("FROM example:local\nWORKDIR /app\n")
        (self.root / "tests").mkdir()
        (self.root / "tests/Dockerfile").write_text("FROM python:3.11-slim\nCOPY . /tests\n")
        (self.root / "tests/test.sh").write_text('mkdir -p /logs/verifier\necho 1 > /logs/verifier/reward.txt\n')
        (self.root / "tests/hidden_assets").mkdir()
        (self.root / "tests/hidden_assets/cases.json").write_text('{"cases": [1]}')
        (self.root / "run-a/verifier").mkdir(parents=True)
        (self.root / "run-a/config.json").write_text('{"task":{"path":"fixture"},"agent":{"name":"nop"}}')
        (self.root / "run-a/result.json").write_text(json.dumps({"finished_at": "2026-09-08T06:00:00Z",
            "exception_info": None, "agent_info": {"name": "nop"}, "verifier_environment_mode": "separate",
            "verifier_result": {"rewards": {"reward": 0.0}}}))
        (self.root / "run-a/verifier/reward.txt").write_text("0.0")
        (self.root / "run-a/trial.log").write_text("Fixture verifier completed")
        for name in ("trajectory_a.json", "trajectory_b.json"):
            (self.root / name).write_text(json.dumps({"duration_seconds": 43200, "rounds": [
                {"round": 1, "policy_name": "fixture", "method_summary": "Synthetic test fixture only.",
                 "status": "ok", "score": 0.2, "failure_reason": None, "retained_best": True,
                 "time": "2026-09-30 10:00:00"}]}))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, data):
        (self.root / name).write_text(json.dumps(data))

    def report(self):
        return qa.build_report(self.root, self.root, qa.inventory(self.root))

    def test_report_uses_portable_paths(self):
        report = self.report()
        self.assertEqual(report["source"]["path"], "artifact")
        self.assertEqual(report["workspace_root"], ".")

    def review(self):
        rows = [qa.item(i, "pass", "测试夹具中的对应内容已人工复核。", ["instruction.md"]) for i in qa.IDS]
        rows[14] = qa.item("QA15", "not_applicable", "跳过。")
        # QA16 is always recalculated from runtime records.
        from test_harbor_review import valid_review
        overview = {
            "optimization_surface": {
                "summary": "测试夹具的有界方法面。",
                "modifiable": ["候选方法"],
                "fixed": ["评分协议"],
                "metric_and_gate": "测试指标越低越好。",
                "evidence": ["instruction.md"],
            },
            "baseline_reference": {
                "baseline_method": "固定线性模型使用完整训练协议。",
                "reference_method": "增加自适应特征变换并使用相同训练预算。",
                "metric": "test_metric",
                "direction": "minimize",
                "paired_runs": [{"seed": seed, "baseline": baseline, "reference": baseline - 2,
                                 "baseline_valid": True, "reference_valid": True}
                                for seed, baseline in ((1, 9.9), (2, 10.0), (3, 10.1))],
                "baseline_mean": 10.0,
                "baseline_sample_std": 0.1,
                "reference_mean": 8.0,
                "reference_sample_std": 0.1,
                "improvement_summary": "原始指标改善2。",
                "quality_gate_summary": "六轮均通过质量门。",
                "evidence": ["instruction.md"],
            },
            "trajectories": [
                {"name": "Agent A", "status": "complete", "source_path": "trajectory_a.json",
                 "packaged_rounds": None, "reported_effective_rounds": None,
                 "duration_hours": 12, "summary": "第一条合成轨迹。",
                 "final_result": "夹具结果。", "evidence": ["trajectory_a.json"]},
                {"name": "Agent B", "status": "complete", "source_path": "trajectory_b.json",
                 "packaged_rounds": None, "reported_effective_rounds": None,
                 "duration_hours": 12, "summary": "第二条合成轨迹。",
                 "final_result": "夹具结果。", "evidence": ["trajectory_b.json"]},
            ],
        }
        format_review = {
            "status": "deviations",
            "summary": "测试夹具仅用于单元测试，未构造完整提交树。",
            "additional_suggestions": [],
        }
        gates = [{"id": key, "status": "pass", "summary": "合成夹具已复核。", "evidence": ["instruction.md"],
                  "reason_code": "REVIEWED", "remediation": "", "acceptance_evidence": ""} for key in ("G01", "G02", "G03")]
        gates[2]["assessment"] = {"direction": "minimize", "formal_seeds": [1, 2, 3], "declared_run_count": 3,
                                  "evaluation_mode": "stochastic", "same_protocol": True, "quality_valid": True,
                                  "upper_bound": 5.0, "threshold_basis": "算法规范3σ_B及[0.15,0.8]。"}
        runtime = {"exception_reason": "", "trajectories": [
            {"name": row["name"], "source_path": row["source_path"], "effective_seconds": 36000,
             "duration_evidence": row["source_path"] + "#.duration_seconds",
             "evidence": [row["source_path"]], "time_accounting": "总历时12h，扣除2h安装和排队后有效10h。",
             "best_method_revalidated": True, "best_method_evidence": [row["source_path"]]}
            for row in overview["trajectories"]]}
        harbor_review = valid_review()
        harbor_review["trial_task_binding"] = {"summary": "Synthetic fixtures refer to this test task, not actual execution.",
                                              "evidence": ["task.toml", "run-a/config.json", "run-a/trial.log"]}
        harbor_review["checks"][4] = {"id": "H05", "status": "pass", "summary": "已复核 NOP Trial 配置。", "evidence": ["run-a/config.json"]}
        harbor_review["checks"][5] = {"id": "H06", "status": "pass", "summary": "NOP Trial 在独立 Verifier 环境中结束。",
                                      "evidence": ["run-a/config.json", "run-a/result.json", "run-a/verifier/reward.txt", "run-a/trial.log"]}
        return {"checks": rows, "harbor": harbor_review, "overview": overview, "format_review": format_review,
                "content_gates": {"schema_version": 1, "checks": gates}, "runtime_review": runtime}

    def test_initial_report_never_passes(self):
        self.assertEqual(self.report()["summary"]["decision"], "INCOMPLETE")

    def test_collected_wall_runtime_is_not_effective_runtime(self):
        self.write("trajectory.json", {"run_duration_seconds": 21600})
        self.assertEqual(self.report()["checks"][15]["status"], "manual")

    def test_short_runtime_fails(self):
        self.write("trajectory.json", {"run_duration_seconds": 21599})
        self.assertEqual(self.report()["checks"][15]["status"], "manual")

    def test_no_summing_independent_runs(self):
        self.write("trajectory.json", [{"run_id": "a", "duration_seconds": 20000}, {"run_id": "b", "duration_seconds": 20000}])
        self.assertEqual(self.report()["checks"][15]["status"], "manual")

    def test_budget_and_uptime_are_not_runtime(self):
        self.write("trajectory.json", {"time_budget_seconds": 50000, "up_s": 50000, "budget": {"duration_seconds": 50000}})
        self.assertFalse([row for row in self.report()["runtime_candidates"] if row["evidence"].startswith("trajectory.json#")])

    def test_trial_duration_does_not_count(self):
        self.write("trajectory.json", [{"trial": 1, "duration_seconds": 50000}])
        self.assertFalse([row for row in self.report()["runtime_candidates"] if row["evidence"].startswith("trajectory.json#")])

    def test_cross_midnight_timestamp(self):
        self.write("trajectory.json", {"started_at": "2026-09-08T18:00:00+08:00", "ended_at": "2026-09-09T06:00:00+08:00"})
        self.assertEqual(self.report()["runtime_candidates"][0]["seconds"], 43200)

    def test_events_require_same_run(self):
        data = [{"event": "run_start", "run_id": "a", "timestamp": 0}, {"event": "run_end", "run_id": "b", "timestamp": 50000}]
        self.write("trajectory.jsonl", data)
        self.assertFalse([row for row in self.report()["runtime_candidates"] if row["evidence"].startswith("trajectory.jsonl#")])
        data[1]["run_id"] = "a"
        self.write("trajectory.jsonl", data)
        self.assertTrue([row for row in self.report()["runtime_candidates"] if row["evidence"].startswith("trajectory.jsonl#")])

    def test_bad_json_is_incomplete(self):
        (self.root / "trajectory.json").write_text("{")
        self.assertEqual(self.report()["checks"][15]["status"], "manual")

    def test_runtime_cannot_be_overridden_by_pass(self):
        review = self.review()
        review["runtime_review"]["trajectories"][1]["effective_seconds"] = 6 * 3600
        report = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(report["checks"][15]["status"], "fail")
        self.assertEqual(report["summary"]["decision"], "FAIL")

    def test_review_runtime_cannot_exceed_collected_raw_duration(self):
        for name in ("trajectory_a.json", "trajectory_b.json"):
            self.write(name, {"run_duration_seconds": 1, "rounds": [{
                "round": 1, "policy_name": "fixture", "method_summary": "Synthetic test fixture only.",
                "status": "ok", "score": 0.2, "failure_reason": None,
                "retained_best": True, "time": "2026-09-30 10:00:00"}]})
        review = self.review()
        for row in review["runtime_review"]["trajectories"]:
            row["duration_evidence"] = row["source_path"] + "#.run_duration_seconds"
            row["effective_seconds"] = 36000
        report = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(report["checks"][15]["status"], "fail")
        self.assertEqual(report["summary"]["decision"], "FAIL")
        self.assertIn("大于 collector", report["checks"][15]["summary"])

    def test_ten_hour_runtime_still_requires_best_method_revalidation(self):
        review = self.review()
        del review["runtime_review"]["trajectories"][0]["best_method_revalidated"]
        del review["runtime_review"]["trajectories"][0]["best_method_evidence"]
        report = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(report["checks"][15]["status"], "manual")
        self.assertEqual(report["summary"]["decision"], "INCOMPLETE")
        self.assertIn("最终方法当前版本", report["checks"][15]["summary"])

    def test_runtime_requires_collector_duration_evidence(self):
        review = self.review()
        del review["runtime_review"]["trajectories"][0]["duration_evidence"]
        report = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(report["checks"][15]["status"], "manual")
        self.assertEqual(report["summary"]["decision"], "INCOMPLETE")

    def test_effective_runtime_uses_ten_hours_for_each_trajectory(self):
        for seconds, expected in ((25199, "fail"), (25200, "manual"), (35999, "manual"), (36000, "pass")):
            review = self.review()
            review["runtime_review"]["trajectories"][1]["effective_seconds"] = seconds
            report = qa.apply_review(self.report(), review, self.root)
            self.assertEqual(report["checks"][15]["status"], expected)

    def test_seven_hour_exception_requires_all_evidence(self):
        review = self.review()
        review["runtime_review"]["exception_reason"] = "当前预算下已验证三个独立方法方向，保留可继续验证方向。"
        review["runtime_review"]["exception_eligibility"] = {
            "non_training": True, "short_iterations": True,
            "basis": "合成任务仅评估固定策略，无训练或微调；日志记录典型单轮数秒。",
            "evidence": ["instruction.md", "trajectory_a.json", "trajectory_b.json"]}
        for row in review["runtime_review"]["trajectories"]:
            row.update(effective_seconds=7*3600, effective_method_cycles=3, next_direction="检验特征变换的非线性扩展。",
                       best_method_revalidated=True, best_method_evidence=[row["source_path"]])
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "pass")
        del review["runtime_review"]["trajectories"][1]["next_direction"]
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "manual")

    def short_iteration_review(self):
        review = self.review()
        runtime = review["runtime_review"]
        runtime["exception_reason"] = "单轮执行很短，已完成多个有效方法闭环并保留后续探索方向。"
        runtime["exception_eligibility"] = {
            "non_training": True, "short_iterations": True,
            "basis": "任务没有训练或微调步骤，实际日志显示典型单轮执行数秒。",
            "evidence": ["instruction.md", "trajectory_a.json", "trajectory_b.json"]}
        for row in runtime["trajectories"]:
            row.update(effective_seconds=8*3600, effective_method_cycles=3, next_direction="继续验证尚未尝试的策略结构。",
                       best_method_revalidated=True, best_method_evidence=[row["source_path"]])
        return review

    def test_nontraining_short_iterations_can_use_eight_hour_exception(self):
        review = self.short_iteration_review()
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][15]["status"], "pass")
        self.assertEqual(result["runtime_review"]["exception_eligibility"], review["runtime_review"]["exception_eligibility"])
        self.assertIn("完全不涉及训练或微调且单轮迭代很短", result["checks"][15]["summary"])

    def test_training_eight_hours_cannot_use_exception_even_with_all_closure_evidence(self):
        review = self.short_iteration_review()
        review["runtime_review"]["exception_eligibility"]["non_training"] = False
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][15]["status"], "fail")
        self.assertEqual(result["summary"]["decision"], "FAIL")
        self.assertIn("不适用 7h 例外", result["checks"][15]["summary"])
        self.assertIn("分别达到至少 10h", result["checks"][15]["remediation"])

    def test_nontraining_long_iterations_cannot_use_eight_hour_exception(self):
        review = self.short_iteration_review()
        review["runtime_review"]["exception_eligibility"]["short_iterations"] = False
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "fail")

    def test_unknown_exception_qualification_or_missing_evidence_cannot_pass(self):
        for field in ("non_training", "short_iterations", "basis", "evidence", "entire_eligibility"):
            with self.subTest(field=field):
                review = self.short_iteration_review()
                if field == "entire_eligibility":
                    del review["runtime_review"]["exception_eligibility"]
                else:
                    del review["runtime_review"]["exception_eligibility"][field]
                result = qa.apply_review(self.report(), review, self.root)
                self.assertEqual(result["checks"][15]["status"], "manual")
                self.assertEqual(result["summary"]["decision"], "INCOMPLETE")

    def test_training_ten_hours_does_not_need_exception_qualification(self):
        review = self.review()
        review["runtime_review"]["exception_eligibility"] = {"non_training": False, "short_iterations": False}
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "pass")
        del review["runtime_review"]["exception_eligibility"]
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "pass")

    def test_one_short_training_trajectory_fails_even_if_other_duration_unknown(self):
        review = self.short_iteration_review()
        review["runtime_review"]["exception_eligibility"]["non_training"] = False
        review["runtime_review"]["trajectories"][1]["effective_seconds"] = None
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][15]["status"], "fail")

    def test_exception_qualification_requires_real_in_bundle_evidence(self):
        review = self.short_iteration_review()
        review["runtime_review"]["exception_eligibility"]["evidence"] = ["missing.log"]
        with self.assertRaisesRegex(ValueError, "evidence path"):
            qa.apply_review(self.report(), review, self.root)

    def test_qa07_08_only_allow_instruction(self):
        self.write("secret.json", {"hidden": "test"})
        for index in (6, 7):
            review = self.review()
            review["checks"][index]["evidence"] = ["secret.json"]
            with self.assertRaisesRegex(ValueError, "only instruction"):
                qa.apply_review(self.report(), review, self.root)

    def test_qa15_cannot_be_checked(self):
        review = self.review()
        review["checks"][14] = qa.item("QA15", "fail", "超过资源限制。")
        with self.assertRaisesRegex(ValueError, "must be skipped"):
            qa.apply_review(self.report(), review, self.root)

    def test_complete_report_and_concise_markdown(self):
        self.write("trajectory.json", {"duration_seconds": 21600})
        report = qa.apply_review(self.report(), self.review(), self.root)
        md = qa.compact_markdown(report)
        self.assertTrue(md.startswith("# 优化面介绍\n"))
        self.assertLess(md.index("# 优化面介绍"), md.index("# Baseline 与 Reference 跑分"))
        self.assertLess(md.index("# Baseline 与 Reference 跑分"), md.index("# 两条轨迹迭代概况"))
        self.assertLess(md.index("# 两条轨迹迭代概况"), md.index("# 格式对齐建议"))
        self.assertLess(md.index("# 格式对齐建议"), md.index("# 质检结论：通过"))
        self.assertEqual(sum(line.startswith("| QA") for line in md.splitlines()), 21)
        self.assertTrue(report["review"]["completed"])
        self.assertIn("Harbor 格式/接口：通过；已有 NOP 自检记录支持当前版本构建与运行可用", md)
        self.assertEqual(report["harbor"]["runtime_status"], "evidence_consistent")

    def test_write_report_creates_identical_txt_and_markdown(self):
        out = self.root.parent / "out-rendered"
        qa.write_report(self.report(), out)
        self.assertEqual((out / "report.txt").read_text(), (out / "report.md").read_text())
        self.assertTrue((out / "return_to_expert.txt").is_file())

    def test_content_failure_precedes_unfinished_but_completed_is_independent(self):
        review = self.review()
        review["content_gates"]["checks"][0].update(status="fail", reason_code="PURE_HYPERPARAMETER_SEARCH",
            summary="当前接口只接收数值权重，不能实现方法变化。", remediation="开放候选模型/损失函数的可执行接口。",
            acceptance_evidence="修改后的题面、接口和具有方法改动的Reference。")
        review["checks"][0].update(status="manual", summary="需核对八章节的完整材料。",
            remediation="提供实际Agent可见instruction。", acceptance_evidence="最终instruction.md。")
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["summary"]["decision"], "FAIL")
        self.assertFalse(result["review"]["completed"])
        self.assertEqual(result["summary"]["counts"]["manual"], 1)
        self.assertEqual(result["summary"]["gate_counts"]["fail"], 1)
        rendered = qa.return_to_expert(result)
        self.assertIn("开放候选模型/损失函数的可执行接口", rendered)
        self.assertIn("复检验收材料", rendered)
        md = qa.compact_markdown(result)
        self.assertIn("前置三门：通过 2 项｜不通过 1 项｜待核验 0 项", md)
        self.assertIn("全部复核完成：否", md)

    def test_missing_new_gate_fields_is_incomplete_in_cli(self):
        review = self.review()
        del review["content_gates"]
        path = self.root.parent / "old-review.json"
        path.write_text(json.dumps(review))
        out = self.root.parent / "old-review-output"
        self.assertEqual(qa.main([str(self.root), "--out-dir", str(out), "--review", str(path)]), 2)
        result = json.loads((out / "report.json").read_text())
        self.assertEqual(result["summary"]["decision"], "INCOMPLETE")
        self.assertFalse(result["review"]["completed"])

    def test_missing_method_description_or_runtime_review_rejected(self):
        for key in ("baseline_method", "reference_method"):
            review = self.review()
            del review["overview"]["baseline_reference"][key]
            with self.assertRaisesRegex(ValueError, key):
                qa.apply_review(self.report(), review, self.root)
        review = self.review()
        del review["runtime_review"]
        with self.assertRaisesRegex(ValueError, "runtime_review"):
            qa.apply_review(self.report(), review, self.root)

    def test_content_missing_formal_scores_cannot_pass(self):
        review = self.review()
        comparison = review["overview"]["baseline_reference"]
        comparison["paired_runs"] = []
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["summary"]["decision"], "INCOMPLETE")
        self.assertEqual(result["content_gates"]["checks"][2]["status"], "manual")

    def test_short_or_unaccounted_effective_time_cannot_be_overridden(self):
        review = self.review()
        del review["runtime_review"]["trajectories"][0]["time_accounting"]
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "manual")
        review = self.review()
        review["runtime_review"]["trajectories"][0]["effective_seconds"] = 13 * 3600
        self.assertEqual(qa.apply_review(self.report(), review, self.root)["checks"][15]["status"], "fail")

    def test_reviewed_failure_must_keep_action_and_acceptance_text(self):
        review = self.review()
        row = review["checks"][2]
        row.update(status="fail", summary="Reference缺少重载验证。", remediation="补充该Reference checkpoint的重载评测。",
                   acceptance_evidence="model/artifact.json和reload.log。")
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][2]["remediation"], row["remediation"])
        self.assertEqual(result["checks"][2]["acceptance_evidence"], row["acceptance_evidence"])
        del row["remediation"]
        with self.assertRaisesRegex(ValueError, "QA03.remediation"):
            qa.apply_review(self.report(), review, self.root)

    def test_gate_table_is_before_original_21_checks(self):
        result = qa.apply_review(self.report(), self.review(), self.root)
        md = qa.compact_markdown(result)
        self.assertLess(md.index("# Baseline 与 Reference 跑分"), md.index("# 前置内容门槛"))
        self.assertLess(md.index("# 前置内容门槛"), md.index("| QA01"))
        self.assertEqual(result["schema_version"], 4)
        self.assertEqual(result["policy"]["revision"], "research-quality-v3")
        self.assertIn("G03 复算：Δ=2", md)
        self.assertIn("U=5", md)
        self.assertIn("归一化分数=0.4", md)

    def test_overview_requires_exactly_two_trajectories(self):
        review = self.review()
        review["overview"]["trajectories"].pop()
        with self.assertRaisesRegex(ValueError, "exactly two"):
            qa.apply_review(self.report(), review, self.root)

    def test_old_review_requires_new_harbor_review(self):
        review = self.review()
        del review["harbor"]
        with self.assertRaisesRegex(ValueError, "six Harbor"):
            qa.apply_review(self.report(), review, self.root)

    def test_qa17_manual_pass_cannot_override_harbor_failure(self):
        self.write("trajectory.json", {"duration_seconds": 21600})
        review = self.review()
        review["harbor"]["checks"][3]["status"] = "fail"
        review["harbor"]["checks"][3]["summary"] = "未写入 Harness reward 文件。"
        review["checks"][16].update(remediation="在tests/test.sh写入reward文件。", acceptance_evidence="提供同trial的config/result/reward/log。")
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][16]["status"], "fail")
        self.assertEqual(result["summary"]["decision"], "FAIL")

    def test_unverified_runtime_prevents_final_pass(self):
        self.write("trajectory.json", {"duration_seconds": 21600})
        review = self.review()
        review["harbor"]["checks"][5]["status"] = "manual"
        review["harbor"]["checks"][5]["summary"] = "已有运行材料不完整，待确认。"
        review["checks"][16].update(remediation="补充该trial缺少的运行材料。", acceptance_evidence="同trial的config/result/reward/log。")
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["summary"]["decision"], "INCOMPLETE")
        self.assertIn("运行或任务版本关联待核实", qa.compact_markdown(result))

    def test_old_two_field_trajectory_cannot_be_overridden_by_pass(self):
        self.write("trajectory_a.json", {"rounds": [{"round": 1, "method_summary": "Old format"}]})
        result = qa.apply_review(self.report(), self.review(), self.root)
        self.assertEqual(result["checks"][17]["status"], "fail")
        self.assertEqual(result["checks"][20]["status"], "fail")
        self.assertEqual(result["summary"]["decision"], "FAIL")

    def test_malformed_selected_trajectory_cannot_pass(self):
        (self.root / "trajectory_b.json").write_text('{"rounds":')
        result = qa.apply_review(self.report(), self.review(), self.root)
        self.assertEqual(result["checks"][20]["status"], "fail")

    def test_eight_field_failure_with_null_score_is_valid(self):
        value = json.loads((self.root / "trajectory_b.json").read_text())
        value["rounds"][0].update(status="timeout", score=None, failure_reason="Timed out", retained_best=False)
        self.write("trajectory_b.json", value)
        result = qa.apply_review(self.report(), self.review(), self.root)
        self.assertEqual(result["checks"][20]["status"], "pass")
        self.assertEqual(result["checks"][17]["status"], "pass")

    def test_unknown_trajectory_state_requires_review(self):
        value = json.loads((self.root / "trajectory_b.json").read_text())
        value["rounds"][0].update(status="custom-state", score=None, failure_reason="Needs interpretation", retained_best=False)
        self.write("trajectory_b.json", value)
        result = qa.apply_review(self.report(), self.review(), self.root)
        self.assertEqual(result["checks"][20]["status"], "manual")
        self.assertEqual(result["summary"]["decision"], "INCOMPLETE")

    def test_explicitly_missing_trajectory_is_failure(self):
        review = self.review()
        review["overview"]["trajectories"][1].update(status="missing", source_path=None, evidence=[])
        review["runtime_review"]["trajectories"][1]["source_path"] = None
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][17]["status"], "fail")
        self.assertEqual(result["checks"][20]["status"], "fail")

    def test_no_nop_blocks_qa17(self):
        import shutil
        shutil.rmtree(self.root / "run-a")
        review = self.review()
        from test_harbor_review import valid_review
        review["harbor"] = valid_review()
        result = qa.apply_review(self.report(), review, self.root)
        self.assertEqual(result["checks"][16]["status"], "fail")
        self.assertEqual(result["summary"]["decision"], "FAIL")
        self.assertEqual(result["harbor"]["runtime_status"], "not_run")
        self.assertIn("动态运行未验证", qa.compact_markdown(result))

    def test_evidence_traversal_rejected(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("evidence")
        review = self.review()
        review["checks"][0]["evidence"] = ["../outside.txt"]
        with self.assertRaises(ValueError):
            qa.apply_review(self.report(), review, self.root)

    def test_missing_row_rejected(self):
        review = self.review()
        review["checks"].pop()
        with self.assertRaises(ValueError):
            qa.apply_review(self.report(), review, self.root)

    def test_cli_precheck_uses_new_policy(self):
        out = self.root.parent / "out"
        script = Path(__file__).with_name("audit_task.py")
        completed = subprocess.run([sys.executable, str(script), str(self.root), "--out-dir", str(out), "--policy", "precheck"], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(report["policy"]["name"], "implementation")
        self.assertEqual(report["summary"]["decision"], "INCOMPLETE")

    def test_missing_input_writes_incomplete_report(self):
        out = self.root.parent / "out"
        result = qa.main([str(self.root / "missing.zip"), "--out-dir", str(out)])
        self.assertEqual(result, 2)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(len(report["checks"]), 21)
        self.assertEqual(report["summary"]["decision"], "INCOMPLETE")

    def test_release_self_check_is_zip_only_immutable_and_records_provenance(self):
        archive = self.root.parent / "submission.zip"
        with zipfile.ZipFile(archive, "w") as bundle:
            for path in self.root.rglob("*"):
                if path.is_file():
                    bundle.write(path, path.relative_to(self.root))
        review_path = self.root.parent / "review.json"
        review_path.write_text(json.dumps(self.review()))
        out = self.root.parent / "release-report"
        args = [str(archive), "--out-dir", str(out), "--review", str(review_path),
                "--release-self-check", "--reviewer-provider", "provider-a",
                "--reviewer-model", "model-a", "--reviewer-version", "2026-10",
                "--session-id", "fresh-session-a", "--clean-context", "--fail-on", "incomplete"]
        entrypoint = Path(__file__).with_name("audit_task.py")
        completed = subprocess.run([sys.executable, str(entrypoint), *args], capture_output=True, text=True)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        report = json.loads((out / "report.json").read_text())
        self.assertEqual(report["summary"]["decision"], "PASS")
        self.assertEqual(report["source"]["kind"], "zip")
        self.assertEqual(report["qa_run"]["artifact_sha256"], report["source"]["sha256"])
        self.assertEqual(report["qa_run"]["skill"], {"name": "autoresearch-qa-skills", "version": "0.3.3"})
        self.assertEqual(report["qa_run"]["reviewer"]["session_id"], "fresh-session-a")
        with self.assertRaises(SystemExit):
            qa.main(args)

    def test_release_self_check_rejects_directory_input(self):
        with self.assertRaises(SystemExit):
            qa.main([str(self.root), "--release-self-check", "--review", "review.json",
                     "--reviewer-provider", "p", "--reviewer-model", "m",
                     "--reviewer-version", "v", "--session-id", "s", "--clean-context"])

    def test_cleanup_only_removes_collector_owned_evidence(self):
        out = self.root.parent / "out-cleanup"
        owned = out / "evidence-old"
        owned.mkdir(parents=True)
        (owned / "file.txt").write_text("temporary")
        out.mkdir(exist_ok=True)
        (out / "inventory.json").write_text(json.dumps({"root": str(owned)}))
        qa.cleanup_previous_evidence(out)
        self.assertFalse(owned.exists())

        outside = self.root.parent / "evidence-outside"
        outside.mkdir()
        (out / "inventory.json").write_text(json.dumps({"root": str(outside)}))
        qa.cleanup_previous_evidence(out)
        self.assertTrue(outside.exists())


if __name__ == "__main__":
    unittest.main()
