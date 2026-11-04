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
"""Exercise the Dockerfile's compiler policy without downloading upstream images."""

import os
import re
import shlex
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BINARIES = ("temporal-server", "temporal", "temporal-sql-tool")


def run_guard(tmp_path: Path, version: str, binary: str) -> subprocess.CompletedProcess[str]:
    """Run the actual shell guard with controlled Go build metadata."""
    checked = tmp_path / "checked"
    checked.mkdir()
    for name in BINARIES:
        (checked / name).write_text(version if name == binary else "go1.26.10")
    tools = tmp_path / "bin"
    tools.mkdir()
    go = tools / "go"
    go.write_text('#!/bin/sh\nprintf \'%s: %s\\n\' "$3" "$(cat "$3")"\n')
    go.chmod(0o755)
    dockerfile = (ROOT / "build/temporal.Dockerfile").read_text()
    stage = dockerfile.split("FROM bootstrap-builder AS checked-upstream\n", 1)[1]
    match = re.search(r"^RUN (set -eu; \\\n.*?^    done)$", stage, flags=re.MULTILINE | re.DOTALL)
    assert match, "Compiler guard must remain covered by this regression test"
    script = match[1].replace("\\\n", "\n").replace("/checked/*", f"{shlex.quote(str(checked))}/*")
    return subprocess.run(
        ["sh", "-c", script],
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "version,accepted",
    [
        ("go1.26.9", False),
        ("go1.26.10", True),
        ("go1.26.11", True),
        ("go1.27.2", False),
        ("go1.27.3", True),
        ("go1.27.4", True),
        ("go1.26.10-X:fieldtrack", True),
        ("go1.27.3-X:fieldtrack", True),
        ("go1.26.9-X:fieldtrack", False),
        ("go1.27.2-X:fieldtrack", False),
        ("go1.25.99", False),
        ("go1.28.0", False),
        ("go1.27rc1", False),
        ("go1.27.3rc1", False),
        ("go1.27.3rc1-X:fieldtrack", False),
        ("go1.27.bad", False),
        ("go1.27.3-custom", False),
        ("devel go1.28-abcdef", False),
        ("", False),
    ],
)
def test_compiler_security_boundaries(tmp_path: Path, version: str, accepted: bool) -> None:
    """Enforce compiler minima while accepting experiment metadata and rejecting prereleases."""
    result = run_guard(tmp_path, version, "temporal-server")
    assert (result.returncode == 0) is accepted, result.stderr


@pytest.mark.parametrize("binary", BINARIES)
def test_each_copied_binary_must_be_patched(tmp_path: Path, binary: str) -> None:
    """Require every copied upstream executable to satisfy the compiler policy."""
    result = run_guard(tmp_path, "go1.26.8", binary)
    assert result.returncode != 0
    assert f"{binary}: compiler go1.26.8" in result.stderr
