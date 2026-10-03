from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import build_qa_release
from validate_route_evals import validate_dataset
from verify_skill_archive import validate_archive


class ReleaseBuildTests(unittest.TestCase):
    def test_release_references_match_canonical_version(self) -> None:
        build_qa_release.validate_version_references(build_qa_release.read_version())

    def test_build_is_deterministic_and_one_skill_per_zip(self) -> None:
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            first_outputs = build_qa_release.build_release(Path(first))
            second_outputs = build_qa_release.build_release(Path(second))
            self.assertEqual([path.name for path in first_outputs], [path.name for path in second_outputs])
            for left, right in zip(first_outputs, second_outputs):
                self.assertEqual(left.read_bytes(), right.read_bytes(), left.name)
            version = build_qa_release.read_version()
            manifest = json.loads(
                (Path(first) / f"autoresearch-qa-skills-{version}.manifest.json").read_text()
            )
            self.assertEqual(manifest["packaging_contract"], "one-skill-per-zip")
            self.assertEqual(len(manifest["artifacts"]), 2)
            for skill in build_qa_release.SKILLS:
                archive = Path(first) / f"{skill}-{version}.zip"
                self.assertEqual(validate_archive(archive, skill), [])

    def test_combined_archive_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "legacy-combined.zip"
            with ZipFile(archive, "w", compression=ZIP_DEFLATED) as bundle:
                bundle.writestr(
                    "autoresearch-task-qa/SKILL.md",
                    "---\nname: autoresearch-task-qa\ndescription: test\n---\n",
                )
                bundle.writestr(
                    "autoresearch-baseline-quality/SKILL.md",
                    "---\nname: autoresearch-baseline-quality\ndescription: test\n---\n",
                )
            errors = validate_archive(archive, "autoresearch-task-qa")
            self.assertTrue(any("top-level" in error for error in errors))
            self.assertTrue(any("exactly one SKILL.md" in error for error in errors))

    def test_build_refuses_stale_combined_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "autoresearch-qa-skills.zip").write_bytes(b"legacy")
            with self.assertRaisesRegex(ValueError, "combined QA archive"):
                build_qa_release.build_release(output)


class RoutingEvalTests(unittest.TestCase):
    def test_checked_in_dataset_is_valid(self) -> None:
        self.assertEqual(validate_dataset(build_qa_release.ROOT / "evals" / "qa-skill-routing.jsonl"), [])

    def test_negative_case_cannot_expect_a_qa_skill(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            dataset = Path(directory) / "bad.jsonl"
            rows = [
                {
                    "id": f"case-{index}",
                    "category": "negative" if index <= 4 else "direct" if index <= 8 else "indirect",
                    "prompt": "这是一个足够长的测试请求文本",
                    "expected_skills": ["autoresearch-task-qa"],
                }
                for index in range(1, 13)
            ]
            dataset.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            errors = validate_dataset(dataset)
            self.assertTrue(any("negative cases" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
