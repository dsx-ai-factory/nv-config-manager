# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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
"""Template source installs bootstrap pip; offline wheels never access the index."""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from ruamel.yaml import YAML

_CHART = Path(__file__).resolve().parents[3] / "deploy/helm"


def _installer_script(plugin_root: Path) -> str:
    if shutil.which("helm") is None or not (_CHART / "charts").is_dir():
        pytest.skip("Helm and vendored chart dependencies are required")
    result = subprocess.run(
        [
            "helm",
            "template",
            "test",
            str(_CHART),
            "--values",
            str(_CHART / "values-ci.yaml"),
            "--show-only",
            "templates/render-service.yaml",
            "--set",
            "renderService.templatePlugins.enabled=true",
            "--set",
            f"renderService.templatePlugins.mountPath={plugin_root}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    for document in YAML(typ="safe", pure=True).load_all(io.StringIO(result.stdout)):
        if document and document.get("kind") == "Deployment":
            for container in document["spec"]["template"]["spec"].get("initContainers", []):
                if container["name"] == "install-template-plugins":
                    return container["args"][0]
    raise AssertionError("Template plugin installer was not rendered")


def _run_installer(
    tmp_path: Path, *, fail_bootstrap: bool = False, fail_ca_setup: bool = False
) -> tuple[int, list[dict]]:
    commands = tmp_path / "bin"
    commands.mkdir()
    log = tmp_path / "pip-calls.jsonl"
    pip = commands / "pip"
    pip.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "with open(os.environ['PIP_CALLS'], 'a') as log:\n"
        "    log.write(json.dumps({'args': sys.argv[1:], 'cafile': os.getenv('SSL_CERT_FILE'), "
        "'capath': os.getenv('SSL_CERT_DIR')}) + '\\n')\n"
        "bootstrap = any(arg.startswith('pip @ ') for arg in sys.argv)\n"
        "sys.exit(7 if bootstrap and os.getenv('FAIL_BOOTSTRAP') == '1' else 0)\n"
    )
    pip.chmod(0o755)
    python = commands / "python"
    python.write_text(
        "#!/bin/sh\n[ \"${FAIL_CA_SETUP:-0}\" = 1 ] && exit 9\nprintf '%s\\n' /patched/pip/cacert.pem\n"
    )
    python.chmod(0o755)
    result = subprocess.run(
        ["sh", "-c", _installer_script(tmp_path / "plugins")],
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "PIP_CALLS": str(log),
            "FAIL_BOOTSTRAP": str(int(fail_bootstrap)),
            "FAIL_CA_SETUP": str(int(fail_ca_setup)),
        },
        capture_output=True,
        text=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
    return result.returncode, calls


def test_offline_wheels_do_not_bootstrap_or_access_the_index(tmp_path: Path) -> None:
    wheels = tmp_path / "plugins/offline/wheels"
    wheels.mkdir(parents=True)
    (wheels / "fixture.whl").touch()
    status, calls = _run_installer(tmp_path)
    assert status == 0
    assert len(calls) == 1
    assert "--no-index" in calls[0]["args"]


def test_source_installs_bootstrap_once_before_all_index_access(tmp_path: Path) -> None:
    for directory in ["first/source", "second"]:
        source = tmp_path / "plugins" / directory
        source.mkdir(parents=True)
        (source / "pyproject.toml").touch()
    status, calls = _run_installer(tmp_path)
    assert status == 0
    assert len(calls) == 5
    requirement = calls[0]["args"][-1]
    assert requirement.startswith("pip @ https://files.pythonhosted.org/")
    assert requirement.endswith(
        "#sha256=fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70"
    )
    for call in calls[1:]:
        assert call["cafile"] == "/patched/pip/cacert.pem"
        assert Path(call["capath"]).is_dir()
        assert not list(Path(call["capath"]).iterdir())


def test_failed_bootstrap_stops_before_build_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "plugins/source-plugin"
    source.mkdir(parents=True)
    (source / "pyproject.toml").touch()
    status, calls = _run_installer(tmp_path, fail_bootstrap=True)
    assert status != 0
    assert len(calls) == 1
    assert not any("hatchling" in call["args"] for call in calls)


def test_failed_ca_lookup_stops_before_build_dependencies(tmp_path: Path) -> None:
    source = tmp_path / "plugins/source-plugin"
    source.mkdir(parents=True)
    (source / "pyproject.toml").touch()
    status, calls = _run_installer(tmp_path, fail_ca_setup=True)
    assert status != 0
    assert len(calls) == 1
    assert not any("hatchling" in call["args"] for call in calls)
