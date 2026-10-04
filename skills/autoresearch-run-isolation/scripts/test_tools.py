import unittest
from unittest.mock import patch
import subprocess

from plan_capacity import estimate, estimate_api_mode
from preflight import validate as validate_contract
from verify_lineage import validate as validate_lineage
from docker_host_preflight import inspect_host


class ToolTests(unittest.TestCase):
    def test_capacity_includes_reacquisition_risk(self):
        result = estimate(10, 100, 2, 23, 5, 1, 0.5, 80)
        self.assertEqual(result["expected_reacquisition_loss"], 40)
        self.assertEqual(result["arithmetic_recommendation"], "hybrid")
        self.assertEqual(result["hourly_reacquisition_break_even_probability"], 0)

    def test_capacity_reports_hourly_risk_break_even(self):
        result = estimate(3, 100, 1, 20, 0, 0, 0.2, 100)
        self.assertEqual(result["hourly_reacquisition_break_even_probability"], 0.37)

    def test_api_mode_chooses_eligible_plan(self):
        result = estimate_api_mode(1000, 0.2, 100, 1000, 0.3, True)
        self.assertEqual(result["arithmetic_recommendation"], "plan")

    def test_api_mode_rejects_ineligible_plan(self):
        result = estimate_api_mode(1000, 0.2, 50, 1000, 0.3, False)
        self.assertEqual(result["arithmetic_recommendation"], "payg")

    def test_preflight_rejects_placeholders(self):
        errors = validate_contract({"task_id": "todo", "tracks": ["same", "same"]})
        self.assertTrue(any("task_id" in error for error in errors))
        self.assertTrue(any("two distinct" in error for error in errors))

    def test_preflight_accepts_complete_contract(self):
        contract = {
            "task_id": "public-task",
            "metric_name": "validation_loss",
            "metric_direction": "minimize",
            "improvement_threshold": 0.01,
            "randomness_protocol": "three fixed training seeds",
            "tracks": ["lane-a", "lane-b"],
            "development_runtime": "compose development profile",
            "target_harness": "pinned target backend version",
            "requires_gpu": True,
            "backend_gpu_evidence": "official capability reference and planned real trial",
            "persistent_snapshot": "object storage snapshot with hashes",
            "stop_loss": "stop after two failed pilots",
            "liveness_protocol": "controller, RPC, receipt and remote job deadline",
            "provider_failure_policy": "hard authorization errors stop with zero credit",
            "durable_evaluator_reconciliation": "reuse completed job before retry",
        }
        self.assertEqual(validate_contract(contract), [])

    def test_preflight_rejects_unproven_builtin_docker(self):
        contract = {
            "task_id": "public-task",
            "metric_name": "score",
            "metric_direction": "maximize",
            "improvement_threshold": 1,
            "randomness_protocol": "fixed paired seeds",
            "tracks": ["lane-a", "lane-b"],
            "development_runtime": "server Docker",
            "target_harness": "pinned backend",
            "persistent_snapshot": "off-host snapshot",
            "stop_loss": "stop after failed pilot",
            "liveness_protocol": "controller, RPC, receipt and remote job deadline",
            "provider_failure_policy": "hard authorization errors stop with zero credit",
            "durable_evaluator_reconciliation": "reuse completed job before retry",
            "server_provides_docker": True,
        }
        errors = validate_contract(contract)
        self.assertTrue(any("docker_host_preflight" in error for error in errors))
        self.assertTrue(any("agent_image_probe" in error for error in errors))

    def test_docker_host_preflight_keeps_dynamic_boundary(self):
        outputs = {
            ("docker", "version", "--format", "{{json .Server}}"): '{"Version":"1"}',
            ("docker", "compose", "version", "--short"): "2.0",
            ("docker", "info", "--format", "{{json .}}"): '{"DockerRootDir":"/tmp","Runtimes":{"runc":{}}}',
        }

        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, outputs[tuple(argv)], "")

        with patch("docker_host_preflight.shutil.disk_usage") as usage:
            usage.return_value.free = 100 * 1024**3
            report = inspect_host(False, 50, runner)
        self.assertTrue(report["static_ready"])
        self.assertIsNone(report["dynamic_ready"])
        self.assertIn("do not prove", report["acceptance_boundary"])

    def test_docker_host_preflight_runs_pinned_container_gpu_probe(self):
        image = "registry.example/probe@sha256:" + "a" * 64
        outputs = {
            ("docker", "version", "--format", "{{json .Server}}"): '{"Version":"1"}',
            ("docker", "compose", "version", "--short"): "2.0",
            ("docker", "info", "--format", "{{json .}}"): (
                '{"DockerRootDir":"/tmp","Runtimes":{"nvidia":{},"runc":{}}}'
            ),
            ("nvidia-smi", "-L"): "GPU 0: Generic GPU",
            ("docker", "image", "inspect", image): "[]",
            (
                "docker", "run", "--rm", "--pull=never", "--gpus", "all",
                image, "nvidia-smi", "-L",
            ): "GPU 0: Generic GPU",
        }

        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, outputs[tuple(argv)], "")

        with patch("docker_host_preflight.shutil.disk_usage") as usage:
            usage.return_value.free = 100 * 1024**3
            report = inspect_host(True, 50, runner, image)
        self.assertTrue(report["static_ready"])
        self.assertTrue(report["dynamic_ready"])
        self.assertEqual(report["dynamic_findings"][-1]["code"], "container_gpu")

    def test_docker_host_preflight_rejects_floating_probe_image(self):
        outputs = {
            ("docker", "version", "--format", "{{json .Server}}"): '{"Version":"1"}',
            ("docker", "compose", "version", "--short"): "2.0",
            ("docker", "info", "--format", "{{json .}}"): (
                '{"DockerRootDir":"/tmp","Runtimes":{"nvidia":{},"runc":{}}}'
            ),
            ("nvidia-smi", "-L"): "GPU 0: Generic GPU",
        }

        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, outputs[tuple(argv)], "")

        with patch("docker_host_preflight.shutil.disk_usage") as usage:
            usage.return_value.free = 100 * 1024**3
            report = inspect_host(True, 50, runner, "registry.example/probe:latest")
        self.assertTrue(report["static_ready"])
        self.assertFalse(report["dynamic_ready"])

    def test_docker_host_preflight_rejects_probe_without_gpu_contract(self):
        outputs = {
            ("docker", "version", "--format", "{{json .Server}}"): '{"Version":"1"}',
            ("docker", "compose", "version", "--short"): "2.0",
            ("docker", "info", "--format", "{{json .}}"): (
                '{"DockerRootDir":"/tmp","Runtimes":{"nvidia":{},"runc":{}}}'
            ),
        }

        def runner(argv):
            return subprocess.CompletedProcess(argv, 0, outputs[tuple(argv)], "")

        with patch("docker_host_preflight.shutil.disk_usage") as usage:
            usage.return_value.free = 100 * 1024**3
            report = inspect_host(False, 50, runner, "sha256:" + "a" * 64)
        self.assertTrue(report["static_ready"])
        self.assertFalse(report["dynamic_ready"])
        self.assertEqual(report["dynamic_findings"][0]["code"], "gpu_probe_contract")

    def test_lineage_rejects_spliced_receipt(self):
        run = self._run()
        run["receipt_run_id"] = "another-run"
        self.assertTrue(any("receipt_run_id" in error for error in validate_lineage({"runs": [run]})))

    def test_lineage_accepts_consistent_trial(self):
        self.assertEqual(validate_lineage({"runs": [self._run()]}), [])

    @staticmethod
    def _run():
        return {
            "run_id": "run-001",
            "role": "lane-a",
            "training_seed": 7,
            "replicate_id": 1,
            "source_sha256": "a" * 64,
            "config_sha256": "b" * 64,
            "receipt_run_id": "run-001",
            "artifact_run_id": "run-001",
        }


if __name__ == "__main__":
    unittest.main()
