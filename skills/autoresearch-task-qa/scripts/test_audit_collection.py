from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest


SCRIPT = Path(__file__).with_name("audit_collection.py")
SPEC = importlib.util.spec_from_file_location("autoresearch_audit_collection", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
COLLECTION = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = COLLECTION
SPEC.loader.exec_module(COLLECTION)


class AuditCollectionTests(unittest.TestCase):
    def test_slugify_is_stable_and_safe(self) -> None:
        self.assertEqual(COLLECTION.slugify("My Task.zip", 3), "03-my-task")
        self.assertEqual(COLLECTION.slugify("任务.zip", 1), "01-artifact")

    def test_strict_status_uses_recorded_adjustment(self) -> None:
        check = {"id": "QA07", "status": "warn"}
        adjustments = {"QA07": {"from": "fail"}}
        self.assertEqual(COLLECTION.strict_status(check, adjustments), "FAIL")
        self.assertEqual(COLLECTION.strict_status(check, {}), "WARN")

    def test_report_review_requires_all_21_conclusions(self) -> None:
        checks = [
            {
                "id": check_id,
                "title": check_id,
                "status": "pass",
                "severity": "high",
                "summary": "reviewed",
                "evidence": [],
                "remediation": "",
            }
            for check_id in COLLECTION.EXPECTED_CHECK_IDS
        ]
        report = {"checks": checks, "summary": {"decision": "PRECHECK-PASS"}}
        self.assertEqual(COLLECTION.validate_report(report), [])
        report["checks"] = checks[:-1]
        self.assertIn("missing checks: QA21", COLLECTION.validate_report(report))

    def test_success_report_cannot_contain_failed_check(self) -> None:
        checks = [
            {
                "id": check_id,
                "title": check_id,
                "status": "pass",
                "severity": "high",
                "summary": "reviewed",
                "evidence": [],
                "remediation": "",
            }
            for check_id in COLLECTION.EXPECTED_CHECK_IDS
        ]
        checks[0]["status"] = "fail"
        issues = COLLECTION.validate_report(
            {"checks": checks, "summary": {"decision": "PRECHECK-PASS"}}
        )
        self.assertTrue(any("contains failed" in issue for issue in issues))

    def passing_report(self):
        checks = [{"id": key, "title": key, "status": "pass", "severity": "info", "summary": "reviewed",
                   "evidence": ["instruction.md"], "remediation": "", "acceptance_evidence": ""}
                  for key in COLLECTION.EXPECTED_CHECK_IDS]
        return {"schema_version": 4, "policy": {"name": "implementation"}, "checks": checks,
                  "summary": {"decision": "PASS"}, "review": {"completed": True},
                  "runtime_review": {"status": "pass"},
                  "overview": {"baseline_reference": {"baseline_method": "linear", "reference_method": "feature transformation"}},
                  "content_gates": {"checks": [{"id": key, "status": "pass"} for key in ("G01", "G02", "G03")]},
                  "harbor": {"static_status": "pass", "qa17_status": "pass", "runtime_status": "evidence_consistent",
                             "checks": [{"id": f"H{i:02d}", "status": "pass" if i != 5 else "not_applicable",
                                         "evidence": ["run-a/config.json"] if i == 6 else []} for i in range(1, 7)],
                             "trial_evidence": {"agent": "nop"},
                             "artifact_contract": {"status": "pass"}, "hidden_review": {"status": "pass"},
                             "path_contract": {"status": "pass", "profile": "teaching-task-root-v1", "findings": []}}}

    def test_new_pass_requires_three_gates_and_runtime_review(self):
        report = self.passing_report()
        self.assertEqual(COLLECTION.validate_report(report), [])
        report["content_gates"]["checks"][0]["status"] = "manual"
        self.assertIn("PASS contains unpassed content gates", COLLECTION.validate_report(report))
        report["content_gates"]["checks"].pop()
        self.assertIn("implementation report requires G01–G03 exactly once", COLLECTION.validate_report(report))
        del report["runtime_review"]
        self.assertIn("PASS requires both effective trajectory durations reviewed", COLLECTION.validate_report(report))

    def test_old_implementation_schema_cannot_be_counted_as_pass(self):
        report = {"checks": [], "schema_version": 3, "policy": {"name": "implementation"}, "summary": {"decision": "PASS"}}
        self.assertIn("implementation report requires schema_version 4", COLLECTION.validate_report(report))

    def test_path_fail_cannot_be_hidden_by_qa17_pass(self):
        report = self.passing_report()
        report["harbor"]["path_contract"]["status"] = "fail"
        self.assertTrue(any("QA17 cannot override" in issue for issue in COLLECTION.validate_report(report)))
        report["harbor"]["path_contract"]["status"] = "pass"
        report["harbor"]["path_contract"]["findings"] = [{"status": "fail", "message": "missing source"}]
        self.assertIn("PASS contains failed Docker path findings", COLLECTION.validate_report(report))

    def test_harbor_runtime_and_checks_must_be_consistent(self):
        report = self.passing_report()
        report["harbor"]["runtime_status"] = "not_run"
        self.assertIn("Harbor runtime_status disagrees with H06", COLLECTION.validate_report(report))
        report["harbor"]["checks"][2]["status"] = "manual"
        self.assertTrue(any("H01–H04" in issue for issue in COLLECTION.validate_report(report)))
        report["harbor"]["checks"].pop()
        self.assertIn("PASS requires H01–H06 exactly once", COLLECTION.validate_report(report))

    def test_manual_contract_requires_actual_resolution_shape(self):
        report = self.passing_report()
        contract = report["harbor"]["path_contract"]
        contract["status"] = "manual"
        self.assertIn("PASS contains unresolved manual Docker path contract", COLLECTION.validate_report(report))
        contract["manual_resolution"] = {"summary": "已人工核对镜像设置。", "evidence": ["task.toml"]}
        self.assertEqual(COLLECTION.validate_report(report), [])
        contract["profile"] = "custom"
        self.assertIn("PASS contains unresolved manual Docker path contract", COLLECTION.validate_report(report))
        contract["adapter_evidence"] = ["adapter.json"]
        self.assertEqual(COLLECTION.validate_report(report), [])

    def test_missing_required_nop_cannot_be_counted_as_pass(self):
        report = self.passing_report()
        report["harbor"]["checks"][5] = {"id": "H06", "status": "not_applicable", "evidence": []}
        report["harbor"]["runtime_status"] = "not_run"
        self.assertIn("PASS requires mandatory NOP evidence and H06 pass", COLLECTION.validate_report(report))

    def test_supplied_runtime_cannot_be_skipped(self):
        report = self.passing_report()
        report["harbor"]["checks"][5] = {"id": "H06", "status": "not_applicable", "evidence": ["run-a/config.json"]}
        report["harbor"]["runtime_status"] = "not_run"
        self.assertIn("H06 cannot skip supplied runtime evidence", COLLECTION.validate_report(report))

    def test_artifact_or_hidden_review_cannot_be_hidden_by_static_pass(self):
        report = self.passing_report()
        report["harbor"]["artifact_contract"]["status"] = "fail"
        report["harbor"]["hidden_review"]["status"] = "manual"
        issues = COLLECTION.validate_report(report)
        self.assertIn("PASS requires consistent submission artifact paths", issues)
        self.assertIn("PASS requires reviewed Hidden supply and grading-use evidence", issues)

    def test_failed_incomplete_review_remains_valid_failure(self):
        report = self.passing_report()
        report["summary"]["decision"] = "FAIL"
        report["review"]["completed"] = False
        report["content_gates"]["checks"][0]["status"] = "fail"
        report["checks"][0]["status"] = "manual"
        self.assertEqual(COLLECTION.validate_report(report), [])


if __name__ == "__main__":
    unittest.main()
