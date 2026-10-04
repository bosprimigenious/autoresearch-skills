import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from authoring_gate import STAGES, required_through, validate


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class AuthoringGateTests(unittest.TestCase):
    def test_later_stage_includes_all_earlier_requirements(self):
        expected = sum((len(fields) for fields in STAGES.values()), 0)
        self.assertEqual(len(required_through("release")), expected)

    def test_placeholder_fails_closed(self):
        evidence = {field: "evidence/ref" for field in required_through("selection")}
        evidence["license_evidence"] = "TBD"
        self.assertEqual(validate("selection", evidence), ["license_evidence"])

    def test_complete_pilot_passes(self):
        evidence = {field: "evidence/ref" for field in required_through("pilot")}
        self.assertEqual(validate("pilot", evidence), [])

    def test_unknown_stage_is_rejected(self):
        with self.assertRaises(ValueError):
            required_through("later")


class ReleaseGateTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.artifact_hash = "a" * 64
        self.reports = [
            self._report("openai", "gpt", "5.2", "session-a"),
            self._report("anthropic", "claude", "4.6", "session-b"),
        ]
        self.evidence = {
            field: "evidence/ref"
            for field in required_through("long_run")
        }
        privacy = self._write_json("privacy.json", {"status": "PASS"})
        manifest = self._write_json(
            "manifest.json", {"artifact_sha256": self.artifact_hash}
        )
        self.evidence["attachment_privacy_report"] = self._ref(privacy)
        self.evidence["artifact_manifest"] = self._ref(manifest)
        self._rewrite_reports_and_consensus()

    def tearDown(self):
        self.tempdir.cleanup()

    def _report(self, provider, model, version, session_id):
        return {
            "source": {"kind": "zip", "sha256": self.artifact_hash},
            "summary": {"decision": "PASS"},
            "qa_run": {
                "skill": {"name": "autoresearch-qa-skills", "version": "0.3.4"},
                "artifact_sha256": self.artifact_hash,
                "input_kind": "zip",
                "clean_context": True,
                "reviewer": {
                    "provider": provider,
                    "model": model,
                    "version": version,
                    "session_id": session_id,
                },
            },
        }

    def _write_json(self, name, payload):
        path = self.root / name
        path.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
        return path

    def _ref(self, path):
        return {"path": path.name, "sha256": digest(path)}

    def _rewrite_reports_and_consensus(self, disagreements=None):
        report_paths = [
            self._write_json(f"qa-{index}.json", report)
            for index, report in enumerate(self.reports)
        ]
        self.evidence["qa_reports"] = [self._ref(path) for path in report_paths]
        consensus = self._write_json(
            "consensus.json",
            {
                "status": "PASS",
                "skill": {"name": "autoresearch-qa-skills", "version": "0.3.4"},
                "artifact_sha256": self.artifact_hash,
                "report_sha256s": [digest(path) for path in report_paths],
                "unresolved_disagreements": disagreements or [],
                "validation_errors": [],
            },
        )
        self.evidence["qa_consensus"] = self._ref(consensus)

    def assert_release_fails(self, text):
        issues = validate("release", self.evidence, self.root)
        self.assertTrue(any(text in issue for issue in issues), issues)

    def test_valid_dual_qa_release_passes(self):
        self.assertEqual(validate("release", self.evidence, self.root), [])

    def test_fake_report_path_fails(self):
        self.evidence["qa_reports"][0]["path"] = "missing.json"
        self.assert_release_fails("file does not exist")

    def test_single_report_fails(self):
        self.evidence["qa_reports"] = self.evidence["qa_reports"][:1]
        self.assert_release_fails("exactly two")

    def test_same_model_identity_fails(self):
        self.reports[1]["qa_run"]["reviewer"].update(
            {"provider": "openai", "model": "gpt", "version": "5.2"}
        )
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("two different AIs")

    def test_same_session_fails(self):
        self.reports[1]["qa_run"]["reviewer"]["session_id"] = "session-a"
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("session_id values must differ")

    def test_different_artifact_fails(self):
        self.reports[1]["source"]["sha256"] = "b" * 64
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("artifact sha256 values differ")

    def test_qa_run_provenance_must_match_source(self):
        self.reports[0]["qa_run"]["artifact_sha256"] = "b" * 64
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("provenance does not match source")

    def test_non_zip_source_fails(self):
        self.reports[0]["source"]["kind"] = "directory"
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("expected zip")

    def test_non_pass_report_fails(self):
        self.reports[0]["summary"]["decision"] = "INCOMPLETE"
        self._rewrite_reports_and_consensus()
        self.assert_release_fails("expected PASS")

    def test_tampered_report_digest_fails(self):
        self.evidence["qa_reports"][0]["sha256"] = "f" * 64
        self.assert_release_fails("digest mismatch")

    def test_unresolved_disagreement_fails(self):
        self._rewrite_reports_and_consensus(["QA16 duration evidence disagrees"])
        self.assert_release_fails("expected an empty list")

    def test_consensus_validation_errors_fail(self):
        consensus_path = self.root / self.evidence["qa_consensus"]["path"]
        consensus = json.loads(consensus_path.read_text(encoding="utf-8"))
        consensus["validation_errors"] = ["reviewer identity missing"]
        consensus_path.write_text(json.dumps(consensus), encoding="utf-8")
        self.evidence["qa_consensus"] = self._ref(consensus_path)
        self.assert_release_fails("validation_errors")

    def test_missing_privacy_report_fails(self):
        self.evidence["attachment_privacy_report"]["path"] = "missing-privacy.json"
        self.assert_release_fails("file does not exist")

    def test_manifest_digest_must_match_file(self):
        self.evidence["artifact_manifest"]["sha256"] = "f" * 64
        self.assert_release_fails("digest mismatch")


if __name__ == "__main__":
    unittest.main()
