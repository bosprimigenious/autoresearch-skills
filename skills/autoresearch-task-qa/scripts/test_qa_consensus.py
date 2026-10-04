import json
from pathlib import Path
import tempfile
import unittest

import qa_consensus as consensus


class ConsensusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifact = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def write_report(self, name, provider, model, version, session, *, decision="PASS", qa_status="pass"):
        path = self.root / name
        report = {
            "source": {"kind": "zip", "sha256": self.artifact},
            "summary": {"decision": decision},
            "qa_run": {
                "skill": {"name": "autoresearch-qa-skills", "version": "0.3.3"},
                "artifact_sha256": self.artifact,
                "input_kind": "zip",
                "clean_context": True,
                "reviewer": {"provider": provider, "model": model, "version": version, "session_id": session},
            },
            "checks": [{"id": "QA01", "status": qa_status}],
            "content_gates": {"checks": [{"id": "G01", "status": "pass"}]},
            "harbor": {"checks": [{"id": "H01", "status": "pass"}]},
        }
        path.write_text(json.dumps(report), encoding="utf-8")
        return path

    def test_two_distinct_clean_passes_produce_compatible_consensus(self):
        left = self.write_report("left.json", "openai", "gpt", "6.1", "session-a")
        right = self.write_report("right.json", "anthropic", "claude", "5", "session-b")
        result, passed = consensus.aggregate([left, right])
        self.assertTrue(passed)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["artifact_sha256"], self.artifact)
        self.assertEqual(result["unresolved_disagreements"], [])
        self.assertEqual(set(result["report_sha256s"]), {row["sha256"] for row in result["qa_reports"]})

    def test_same_model_or_session_fails_closed(self):
        left = self.write_report("left.json", "openai", "gpt", "6.1", "same")
        right = self.write_report("right.json", "openai", "gpt", "6.1", "same")
        result, passed = consensus.aggregate([left, right])
        self.assertFalse(passed)
        self.assertEqual(result["status"], "NOT_READY")
        self.assertTrue(result["validation_errors"])

    def test_verdict_disagreement_is_explicit_and_nonpassing(self):
        left = self.write_report("left.json", "openai", "gpt", "6.1", "session-a")
        right = self.write_report("right.json", "openai", "gpt", "6.2", "session-b", qa_status="fail")
        result, passed = consensus.aggregate([left, right])
        self.assertFalse(passed)
        self.assertEqual(result["unresolved_disagreements"][0]["check"], "qa:QA01")

    def test_nonpass_report_cannot_form_pass_consensus(self):
        left = self.write_report("left.json", "openai", "gpt", "6.1", "session-a")
        right = self.write_report("right.json", "openai", "gpt", "6.2", "session-b", decision="FAIL")
        result, passed = consensus.aggregate([left, right])
        self.assertFalse(passed)
        self.assertIn("report 2 decision is not PASS", result["validation_errors"])


if __name__ == "__main__":
    unittest.main()
