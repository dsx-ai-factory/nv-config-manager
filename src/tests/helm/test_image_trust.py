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
"""The exported-image audit also rejects roots outside the combined CA bundle."""

import hashlib
import re
import runpy
import ssl
from pathlib import Path

import certifi
import pytest

_CHECKER = Path(__file__).resolve().parents[3] / "build/python-ca.py"


@pytest.mark.parametrize("location", ["bundle", "individual", "hashed", "clean"])
def test_exported_ca_store(tmp_path: Path, location: str) -> None:
    checker = runpy.run_path(str(_CHECKER))
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        Path(certifi.where()).read_text(),
        flags=re.DOTALL,
    )
    suspect = certificates.pop()
    # Use a real certificate as a synthetic distrusted root; no network fixture.
    checker["BLOCKED_ROOTS"].add(hashlib.sha256(ssl.PEM_cert_to_DER_cert(suspect)).hexdigest())
    bundle = tmp_path / "etc/ssl/certs/ca-certificates.crt"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("\n".join(certificates))
    if location == "bundle":
        bundle.write_text(bundle.read_text() + "\n" + suspect)
    elif location == "individual":
        anchor = tmp_path / "usr/share/ca-certificates/stale.crt"
        anchor.parent.mkdir(parents=True)
        anchor.write_text(suspect)
        # docker cp retains absolute symlinks. Audit their exported targets too.
        (bundle.parent / "stale.pem").symlink_to("/usr/share/ca-certificates/stale.crt")
    elif location == "hashed":
        (bundle.parent / "deadbeef.0").write_text(suspect)

    if location == "clean":
        checker["verify_directory"](tmp_path)
    else:
        with pytest.raises(SystemExit, match="Affected emSign trust anchor remains"):
            checker["verify_directory"](tmp_path)


def test_missing_exported_bundle_fails(tmp_path: Path) -> None:
    checker = runpy.run_path(str(_CHECKER))
    with pytest.raises(FileNotFoundError):
        checker["verify_directory"](tmp_path)
