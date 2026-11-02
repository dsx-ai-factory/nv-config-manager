# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Exercise audit isolation and the binding between checked and published images."""

import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import certifi
import pytest
import yaml

type Gate = tuple[Path, Path, dict[str, str]]

ROOT = Path(__file__).resolve().parents[2]
IMAGES = (
    "nv-config-manager",
    "nv-config-manager-kea",
    "nv-config-manager-kea-admin",
    "nv-config-manager-ui",
    "nv-config-manager-nautobot",
    "nv-config-manager-nats-ready",
    "nv-config-manager-temporal",
    "nv-config-manager-temporal-bootstrap",
    "nv-config-manager-temporal-ui",
)


@pytest.fixture
def gate(tmp_path: Path) -> Gate:
    """Keep real audit scripts, replacing only Docker and artifact transport."""
    repo = tmp_path / "trusted"
    for name in (
        ".gitlab/ci/scripts/image_trust_gate.sh",
        ".gitlab/ci/scripts/promote_push_images.sh",
        "build/check-image-trust.sh",
        "build/python-ca.py",
    ):
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    binaries = tmp_path / "bin"
    binaries.mkdir()
    shutil.copyfile(certifi.where(), binaries / "roots.pem")
    (binaries / "arch").write_text("amd64")
    (binaries / "docker").write_text(
        "#!/bin/bash\n"
        'set -eu\nbase="$(dirname "$0")"\n'
        'printf "%s\\n" "$*" >> "$base/calls"\n'
        'case "$1" in\n'
        '  buildx) if [[ "$3" == inspect ]]; then\n'
        '    digit=a; [[ "$4" != *-arm64 ]] || digit=b\n'
        '    printf \'{"digest":"sha256:%s"}\\n\' "$(printf %064d 0 | tr 0 "$digit")"\n'
        "  fi ;;\n"
        '  image) if [[ "$2" == inspect ]]; then\n'
        '    if [[ "$4" == *Architecture* ]]; then printf "linux/%s\\n" "$(cat "$base/arch")"\n'
        '    else printf "sha256:%064d\\n" 0; fi\n'
        "  fi ;;\n"
        "  create|cp|run|rm)\n"
        '    [[ -z "${AUDIT_TEST_SECRET:-}" ]] || exit 90\n'
        '    [[ ! -e "$DOCKER_CONFIG/config.json" ]] || exit 91\n'
        '    case "$1" in\n'
        '      create) [[ ! -f "$base/fail-audit" ]] || exit 92; echo container-id ;;\n'
        '      cp) mkdir -p "${!#}"\n'
        '          if [[ "$2" == *:/etc/ssl/certs ]]; then cp "$base/roots.pem" "${!#}/ca-certificates.crt"; fi ;;\n'
        '      run) [[ "$*" == *"--network none"* && "$*" == *"--read-only"* ]] || exit 93\n'
        '           cat "$base/roots.pem" ;;\n'
        "    esac ;;\n"
        "esac\n"
    )
    # setup-python's Linux runtime needs its lib directory after env -i.
    # Supply it only in this test double; keep the production audit isolated.
    python_libdir = shlex.quote(str(Path(sys.base_prefix) / "lib"))
    (binaries / "uv").write_text(
        "#!/bin/bash\nset -eu\n"
        '[[ -z "${AUDIT_TEST_SECRET:-}" ]] || exit 94\n'
        "shift 5\n"
        f"export LD_LIBRARY_PATH={python_libdir}\n"
        f'exec {shlex.quote(sys.executable)} "$@"\n'
    )
    (binaries / "curl").write_text(
        '#!/bin/bash\nset -eu\nbase="$(dirname "$0")"\n'
        'while [[ "$1" != -o ]]; do shift; done\n'
        'cp "$base/artifacts.zip" "$2"\n'
    )
    for name in ("docker", "uv", "curl"):
        (binaries / name).chmod(0o755)
    with zipfile.ZipFile(binaries / "artifacts.zip", "w") as archive:
        for image in IMAGES:
            archive.writestr(f"images/{image}.tar.gz", f"archive-{image}")
        archive.writestr("promote.env", "PR_SHORT_SHA=attacker\n")
        archive.writestr("../../escape", "must not be extracted")
    (repo / "promote.env").write_text(
        "PR_SHORT_SHA=12345678\nPROMOTE_VERSION=0.0.0-pr424.12345678\n"
        + "".join(f"BUILD_JOB_ID_{image.upper().replace('-', '_')}=7\n" for image in IMAGES)
    )
    env = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "AUDIT_TEST_SECRET": "must-not-reach-the-audit",
        "CI_PROJECT_DIR": str(repo),
        "CI_COMMIT_SHORT_SHA": "12345678",
        "CI_API_V4_URL": "https://gitlab.example/api/v4",
        "CI_PROJECT_ID": "7",
        "CI_JOB_TOKEN": "fixture-token",
        "NVCM_IMAGE_REPOSITORY": "registry.example/nvcm",
    }
    return repo, binaries, env


def run_gate(gate: Gate, *args: str) -> subprocess.CompletedProcess[str]:
    repo, _, env = gate
    return subprocess.run(
        ["bash", str(repo / ".gitlab/ci/scripts/image_trust_gate.sh"), *args],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_release_manifests_use_both_audited_digests(gate: Gate) -> None:
    repo, binaries, _ = gate
    for arch in ("amd64", "arm64"):
        (binaries / "arch").write_text(arch)
        result = run_gate(gate, "main", arch)
        assert result.returncode == 0, result.stderr
    (binaries / "calls").write_text("")
    result = run_gate(gate, "manifests")
    assert result.returncode == 0, result.stderr
    calls = (binaries / "calls").read_text().splitlines()
    assert len(calls) == len(IMAGES)
    assert all(f"@sha256:{'a' * 64}" in call and f"@sha256:{'b' * 64}" in call for call in calls)
    assert all("imagetools create" in call for call in calls)
    (repo / "audited-images/arm64.txt").unlink()
    (binaries / "calls").write_text("")
    assert run_gate(gate, "manifests").returncode != 0
    assert not (binaries / "calls").read_text()


@pytest.mark.parametrize("mode", [("main", "amd64"), ("promote",)])
def test_audit_failure_stops_the_gate(gate: Gate, mode: tuple[str, ...]) -> None:
    _, binaries, _ = gate
    (binaries / "fail-audit").touch()
    assert run_gate(gate, *mode).returncode != 0
    assert "push " not in (binaries / "calls").read_text()


def test_promotion_rejects_changed_archive_before_loading_or_pushing(gate: Gate) -> None:
    repo, binaries, env = gate
    result = run_gate(gate, "promote")
    assert result.returncode == 0, result.stderr
    assert len((repo / "audited-pr-images.sha256").read_text().splitlines()) == len(IMAGES)
    assert (repo / "promote.env").read_text().startswith("PR_SHORT_SHA=12345678\n")
    assert not (repo.parent / "escape").exists()
    with zipfile.ZipFile(binaries / "artifacts.zip", "w") as archive:
        archive.writestr("images/nv-config-manager.tar.gz", "changed after audit")
    (binaries / "calls").write_text("")
    result = subprocess.run(
        ["bash", str(repo / ".gitlab/ci/scripts/promote_push_images.sh")],
        cwd=repo,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "FAILED" in result.stdout
    assert "load " not in (binaries / "calls").read_text()
    assert "push " not in (binaries / "calls").read_text()


def test_promotion_requires_a_successful_independent_audit() -> None:
    config = yaml.safe_load((ROOT / ".gitlab/ci/promote-test-envs.yml").read_text())
    push = config["test-promote-push-images"]
    assert {"job": "test-promote-audit-images", "artifacts": True} in push["needs"]
    assert push["rules"][0]["when"] == "on_success"
    audit = config["test-promote-audit-images"]
    assert "dgxc-corp-restricted" in audit["tags"]
    assert not audit.get("allow_failure", False)
    # Both build jobs must succeed before the release manifest job is eligible.
    build = yaml.safe_load((ROOT / ".gitlab/ci/build.yml").read_text())
    assert build["docker-manifest-main"]["needs"] == [
        "docker-build-main-amd64",
        "docker-build-main-arm64",
    ]
