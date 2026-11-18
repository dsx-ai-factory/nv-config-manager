# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise resource publication without reaching a registry or exposing secrets."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / ".gitlab/ci/scripts/publish_installer_resource.sh"


@pytest.mark.parametrize("failure", ["", "checksum", "existing", "missing-token"])
def test_publication_checks_inputs_before_upload(tmp_path: Path, failure: str) -> None:
    archive = tmp_path / "nvcm-installer-1.2.3.tar.gz"
    archive.write_bytes(b"offline distribution")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_name(archive.name + ".sha256").write_text(
        f"{'0' * 64 if failure == 'checksum' else digest}  {archive.name}\n"
    )
    calls = tmp_path / "calls"
    ngc = tmp_path / "ngc"
    ngc.write_text("""#!/usr/bin/env bash
set -eu
printf '%s\\n' "$*" >> "$CALLS"
if [[ "$*" == *'info '* ]]; then
    [[ "$*" == *':1.2.3' && "$EXISTING" == true ]]
elif [[ "$*" == *'upload-version'* ]]; then
    [[ "$NGC_CLI_API_KEY" == test-secret ]]
    [[ -f "$5/nvcm-installer-1.2.3.tar.gz" ]]
    [[ -f "$5/nvcm-installer-1.2.3.tar.gz.sha256" ]]
fi
""")
    ngc.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT), "1.2.3", str(tmp_path)],
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "CALLS": str(calls),
            "EXISTING": str(failure == "existing").lower(),
            "NVCM_INSTALLER_NGC_PATH": "example/team",
            "NVCM_INSTALLER_NGC_TOKEN_VAR": "RESOURCE_TOKEN",
            "RESOURCE_TOKEN": "" if failure == "missing-token" else "test-secret",
        },
        capture_output=True,
        text=True,
    )
    log = calls.read_text() if calls.exists() else ""
    assert "test-secret" not in result.stdout + result.stderr + log
    if failure:
        assert result.returncode != 0
        assert "upload-version" not in log
    else:
        assert result.returncode == 0, result.stderr
        assert "upload-version" in log
