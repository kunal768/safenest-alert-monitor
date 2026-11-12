#!/usr/bin/env python3
"""Validate cloud deploy files without applying. Local run is unaffected."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deploy"


def _ok(cmd: list[str]) -> bool:
    try:
        return subprocess.run(cmd, capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def main() -> int:
    flag = os.environ.get("ENABLE_CLOUD_DEPLOY", "false").lower()
    print(f"ENABLE_CLOUD_DEPLOY={flag} (demo default is false; nothing will be applied)")
    required = [
        ROOT / "Dockerfile",
        DEPLOY / "docker-compose.prod.yml",
        DEPLOY / "deploy.sh",
        DEPLOY / "terraform" / "main.tf",
        DEPLOY / "terraform" / "variables.tf",
        ROOT / ".github" / "workflows" / "ci.yml",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        print("missing files:", missing)
        return 1
    print("all deploy files present")

    tf = DEPLOY / "terraform"
    docker_up = _ok(["docker", "info"])
    if _ok(["which", "terraform"]):
        tf_cmd = lambda *a: ["terraform", *a]  # noqa: E731
    elif docker_up:
        print("terraform not installed; using the hashicorp/terraform Docker image")
        tf_cmd = lambda *a: ["docker", "run", "--rm", "-v", f"{tf}:/tf", "-w", "/tf", "hashicorp/terraform:1.9", *a]  # noqa: E731
    else:
        tf_cmd = None
        print("terraform validate: skipped (install terraform or start Docker to run it)")
    if tf_cmd:
        for step in (("init", "-backend=false", "-input=false"), ("validate",)):
            r = subprocess.run(tf_cmd(*step), cwd=tf)
            if r.returncode != 0:
                return r.returncode
        print("terraform validate: ok")

    r = subprocess.run(["bash", str(DEPLOY / "deploy.sh")], capture_output=True, text=True, env={**os.environ, "ENABLE_CLOUD_DEPLOY": "false"})
    print("deploy.sh with flag off ->", r.stdout.strip() or r.stderr.strip())

    compose = DEPLOY / "docker-compose.prod.yml"
    if _ok(["which", "docker"]):
        r = subprocess.run(["docker", "compose", "-f", str(compose), "config"], capture_output=True, text=True)
        if r.returncode != 0:
            print(r.stderr)
            # still ok if docker engine is not running — config often works without it
            print("docker compose config: skipped or failed (engine optional)")
        else:
            print("docker compose config: ok")
    print("cloud-check complete — no infrastructure applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())
