#!/usr/bin/env python3
"""Host preflight with an optional ephemeral NVIDIA container probe."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import Callable


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, timeout=30, check=False)


def inspect_host(
    requires_gpu: bool,
    minimum_free_gib: float,
    runner: Runner = _run,
    gpu_probe_image: str | None = None,
) -> dict[str, object]:
    findings: list[dict[str, str]] = []
    dynamic_findings: list[dict[str, str]] = []

    def command(code: str, argv: list[str]) -> str | None:
        completed = runner(argv)
        if completed.returncode != 0:
            findings.append({"code": code, "status": "FAIL", "detail": completed.stderr.strip()[-500:]})
            return None
        findings.append({"code": code, "status": "PASS", "detail": completed.stdout.strip()[-500:]})
        return completed.stdout.strip()

    server = command("docker_server", ["docker", "version", "--format", "{{json .Server}}"])
    compose = command("compose_plugin", ["docker", "compose", "version", "--short"])
    info_raw = command("docker_info", ["docker", "info", "--format", "{{json .}}"])

    root_dir = None
    runtimes: list[str] = []
    if info_raw:
        try:
            info = json.loads(info_raw)
            root_dir = info.get("DockerRootDir")
            runtime_value = info.get("Runtimes", {})
            if isinstance(runtime_value, dict):
                runtimes = sorted(map(str, runtime_value))
        except (TypeError, json.JSONDecodeError):
            findings.append({"code": "docker_info_json", "status": "FAIL", "detail": "unparseable JSON"})

    free_gib = None
    if root_dir:
        try:
            free_gib = shutil.disk_usage(Path(root_dir)).free / 1024**3
            findings.append({
                "code": "docker_storage",
                "status": "PASS" if free_gib >= minimum_free_gib else "FAIL",
                "detail": f"free_gib={free_gib:.2f}; required_gib={minimum_free_gib:.2f}",
            })
        except OSError as exc:
            findings.append({"code": "docker_storage", "status": "FAIL", "detail": str(exc)})
    else:
        findings.append({"code": "docker_storage", "status": "FAIL", "detail": "DockerRootDir unavailable"})

    if requires_gpu:
        command("host_gpu", ["nvidia-smi", "-L"])
        findings.append({
            "code": "nvidia_runtime",
            "status": "PASS" if "nvidia" in runtimes else "FAIL",
            "detail": "runtime registered" if "nvidia" in runtimes else "runtime not registered",
        })

    if gpu_probe_image and not requires_gpu:
        dynamic_findings.append({
            "code": "gpu_probe_contract",
            "status": "FAIL",
            "detail": "gpu probe requires requires_gpu=true",
        })
    elif gpu_probe_image:
        pinned = gpu_probe_image.startswith("sha256:") or "@sha256:" in gpu_probe_image
        if not pinned:
            dynamic_findings.append({
                "code": "gpu_probe_image",
                "status": "FAIL",
                "detail": "probe image must be pinned by digest",
            })
        else:
            inspect = runner(["docker", "image", "inspect", gpu_probe_image])
            dynamic_findings.append({
                "code": "gpu_probe_image_local",
                "status": "PASS" if inspect.returncode == 0 else "FAIL",
                "detail": (
                    "pinned image available locally"
                    if inspect.returncode == 0
                    else inspect.stderr.strip()[-500:] or "pinned image unavailable locally"
                ),
            })
            if inspect.returncode == 0:
                probe = runner([
                    "docker", "run", "--rm", "--pull=never", "--gpus", "all",
                    gpu_probe_image, "nvidia-smi", "-L",
                ])
                dynamic_findings.append({
                    "code": "container_gpu",
                    "status": "PASS" if probe.returncode == 0 else "FAIL",
                    "detail": (probe.stdout if probe.returncode == 0 else probe.stderr).strip()[-500:],
                })

    static_ready = all(item["status"] == "PASS" for item in findings)
    dynamic_ready = None if not gpu_probe_image else all(
        item["status"] == "PASS" for item in dynamic_findings
    )
    return {
        "static_ready": static_ready,
        "dynamic_ready": dynamic_ready,
        "docker_server": server,
        "compose_version": compose,
        "docker_root": root_dir,
        "free_gib": None if free_gib is None else round(free_gib, 2),
        "runtimes": runtimes,
        "findings": findings,
        "dynamic_findings": dynamic_findings,
        "acceptance_boundary": (
            "Host and generic container checks do not prove either task image builds, "
            "preserves isolation or completes a GPU trial in the target harness/backend."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requires-gpu", action="store_true")
    parser.add_argument("--minimum-free-gib", type=float, default=50)
    parser.add_argument(
        "--gpu-probe-image",
        help="locally available digest-pinned image for an ephemeral docker --gpus probe",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    if args.minimum_free_gib < 0:
        parser.error("--minimum-free-gib must be non-negative")
    if args.gpu_probe_image and not args.requires_gpu:
        parser.error("--gpu-probe-image requires --requires-gpu")
    report = inspect_host(
        args.requires_gpu,
        args.minimum_free_gib,
        gpu_probe_image=args.gpu_probe_image,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        args.out.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0 if report["static_ready"] and report["dynamic_ready"] is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
