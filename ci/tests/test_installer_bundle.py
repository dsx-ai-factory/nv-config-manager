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
"""Verify standalone bundle checksums with GNU and macOS-style host tools."""

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("gnu_checksum", [True, False])
def test_bundle_checksum_tools(tmp_path: Path, gnu_checksum: bool) -> None:
    repository = tmp_path / "repository"
    scripts = repository / "deploy/airgapped"
    scripts.mkdir(parents=True)
    (repository / "deploy/helm/files").mkdir(parents=True)
    (repository / "deploy/helm/files/python-tooling.sh").write_text("")
    (repository / "installer").mkdir()
    (repository / "installer/README.md").write_text("fixture")
    (repository / "LICENSE").write_text("fixture")
    builder = scripts / "create-installer.sh"
    shutil.copyfile(ROOT / "deploy/airgapped/create-installer.sh", builder)
    # Keep the real archive/checksum operations; dependency downloads and the
    # installed CLI are independent of the host's checksum command selection.
    (scripts / "installer-packaging.sh").write_text("""package_installer() {
    mkdir -p "$BUILD_DIR/installer"
    cat > "$BUILD_DIR/installer/install.sh" <<'BOOTSTRAP'
#!/bin/bash
set -eu
touch "$BOOTSTRAP_MARKER"
mkdir -p "$NV_CONFIG_MANAGER_VENV/bin"
ln -s /bin/true "$NV_CONFIG_MANAGER_VENV/bin/python"
ln -s /bin/true "$(dirname "$0")/nvcm-installer"
BOOTSTRAP
}
""")
    binaries = tmp_path / "bin"
    binaries.mkdir()
    for command in (
        "dirname",
        "mkdir",
        "mktemp",
        "rm",
        "cp",
        "mv",
        "tar",
        "gzip",
        "basename",
        "bash",
        "cat",
        "touch",
        "ln",
    ):
        executable = shutil.which(command)
        assert executable is not None
        (binaries / command).symlink_to(executable)
    checksum = shutil.which("sha256sum")
    assert checksum is not None
    if gnu_checksum:
        (binaries / "sha256sum").symlink_to(checksum)
    else:
        # Emulate stock macOS: shasum is available, sha256sum is absent from PATH.
        shasum = binaries / "shasum"
        shasum.write_text(f'''#!/bin/bash
set -eu
[[ "$1" == -a && "$2" == 256 ]]
shift 2
exec "{checksum}" "$@"
''')
        shasum.chmod(0o755)
    output = tmp_path / "output"
    marker = tmp_path / "bootstrapped"
    env = {**os.environ, "PATH": str(binaries), "BOOTSTRAP_MARKER": str(marker)}
    built = subprocess.run(
        ["/bin/bash", str(builder), "1.2.3", str(output)], env=env, capture_output=True, text=True
    )
    assert built.returncode == 0, built.stderr
    archive = output / "nvcm-installer-1.2.3.tar.gz"
    expected = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert (output / (archive.name + ".sha256")).read_text().split() == [expected, archive.name]
    verifier = ROOT / ".gitlab/ci/scripts/test_installer_bundle.sh"
    verified = subprocess.run(
        ["/bin/bash", str(verifier), str(output)], env=env, capture_output=True, text=True
    )
    assert verified.returncode == 0, verified.stderr
    assert marker.exists()
    marker.unlink()
    with archive.open("ab") as stream:
        stream.write(b"corrupted")
    rejected = subprocess.run(
        ["/bin/bash", str(verifier), str(output)], env=env, capture_output=True, text=True
    )
    assert rejected.returncode != 0
    assert not marker.exists(), "Corrupt archive reached bootstrap"
