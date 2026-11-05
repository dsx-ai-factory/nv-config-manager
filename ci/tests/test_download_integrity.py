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
"""Check that downloads fail before a binary or repository key can be used."""

import hashlib
import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
PAYLOAD = b"verified fixture\n"


def run_download(
    tmp_path: Path, script: str, failure: str, functions: str
) -> subprocess.CompletedProcess[str]:
    """Substitute fixture bytes and paths, retaining the real checksum commands."""
    fixture = tmp_path / "download"
    fixture.write_bytes(b"tampered" if failure == "checksum" else PAYLOAD)
    for path in ("usr/local/bin", "usr/share/keyrings", "etc/apt/sources.list.d"):
        (tmp_path / path).mkdir(parents=True, exist_ok=True)
    (tmp_path / "etc/os-release").write_text("ID=ubuntu\nVERSION_CODENAME=noble\n")
    script = re.sub(r"\b[0-9a-f]{64}\b", hashlib.sha256(PAYLOAD).hexdigest(), script)
    script = script.replace("/usr/", f"{tmp_path}/usr/").replace("/etc/", f"{tmp_path}/etc/")
    return subprocess.run(
        ["bash", "-c", functions + "\n" + script],
        env={
            **os.environ,
            "FIXTURE": str(fixture),
            "DOWNLOAD_FAILURE": failure,
            "KEY_IMPORTED": str(tmp_path / "key-imported"),
        },
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "filename,job",
    [("release.yml", "helm-publish-release"), ("airgapped.yml", ".airgapped-template")],
)
@pytest.mark.parametrize("failure", ["", "checksum", "download"])
def test_yq_is_verified_before_chmod(tmp_path: Path, filename: str, job: str, failure: str) -> None:
    config = yaml.safe_load((ROOT / ".gitlab/ci" / filename).read_text())
    block = config[job]["before_script"][0]
    # Keep the block's own error mode and the actual yq installation commands.
    installation = block[block.index("YQ_VERSION=") :].split("chmod +x /usr/local/bin/yq", 1)[0]
    script = block.splitlines()[0] + "\n" + installation + "chmod +x /usr/local/bin/yq\n"
    result = run_download(
        tmp_path,
        script,
        failure,
        'wget() { cp "$FIXTURE" "$2"; [ "$DOWNLOAD_FAILURE" != download ]; }',
    )
    assert (result.returncode == 0) is (not failure), result.stdout + result.stderr
    assert os.access(tmp_path / "usr/local/bin/yq", os.X_OK) is (not failure)


@pytest.mark.parametrize("repo,codename", [("kea-2-6", "noble"), ("stork", "jammy")])
@pytest.mark.parametrize("failure", ["", "checksum", "download"])
def test_isc_key_is_verified_before_import(
    tmp_path: Path, repo: str, codename: str, failure: str
) -> None:
    script_name = "setup.kea.deb.sh" if repo == "kea-2-6" else "setup.stork.deb.sh"
    result = run_download(
        tmp_path,
        (ROOT / "build" / script_name).read_text(),
        failure,
        """
apt-get() { :; }
curl() { cp "$FIXTURE" "$4"; [ "$DOWNLOAD_FAILURE" != download ]; }
gpg() { touch "$KEY_IMPORTED" "$5"; }
""",
    )
    assert (result.returncode == 0) is (not failure), result.stdout + result.stderr
    assert (tmp_path / "key-imported").exists() is (not failure)
    sources = tmp_path / f"etc/apt/sources.list.d/isc-{repo}.list"
    if failure:
        assert not sources.exists()
    else:
        assert sources.read_text() == (
            f"deb [signed-by={tmp_path}/usr/share/keyrings/isc-{repo}-archive-keyring.gpg] "
            f"https://dl.cloudsmith.io/public/isc/{repo}/deb/ubuntu {codename} main\n"
        )
