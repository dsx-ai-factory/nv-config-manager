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
"""Refresh Python CA stores and audit certificate directories exported from images."""

import hashlib
import re
import shutil
import ssl
import sys
from pathlib import Path

# Public root certificate SHA-256 fingerprints from the emSign incident report.
# https://emudhra.we-have-a-problem.com/roots.json
BLOCKED_ROOTS = {
    "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b",  # emSign ECC G3
    "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4",  # AAA cross-sign
}
CA_DIRECTORIES = (Path("/etc/ssl/certs"), Path("/usr/share/ca-certificates"))


def verify_bundle(path: str = "/etc/ssl/certs/ca-certificates.crt") -> None:
    context = ssl.create_default_context(cafile=path)
    fingerprints = {
        hashlib.sha256(cert).hexdigest() for cert in context.get_ca_certs(binary_form=True)
    }
    if len(fingerprints) < 100:
        raise SystemExit("Public CA bundle is incomplete")
    if fingerprints & BLOCKED_ROOTS:
        raise SystemExit("Affected emSign trust anchor remains in CA bundle")


def verify_directory(directory: Path) -> None:
    """Check the bundle and individual anchors, including stale capath targets."""
    verify_bundle(str(directory / "etc/ssl/certs/ca-certificates.crt"))
    for path in directory.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        for pem in re.findall(
            rb"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
            path.read_bytes(),
            flags=re.DOTALL,
        ):
            fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(pem.decode("ascii"))).hexdigest()
            if fingerprint in BLOCKED_ROOTS:
                raise SystemExit(f"Affected emSign trust anchor remains in {path}")


if __name__ == "__main__":
    if sys.argv[1:] == ["--reset"]:
        # Clear both directories so old hashed CA paths cannot survive the replacement.
        for directory in CA_DIRECTORIES:
            if directory.exists():
                shutil.rmtree(directory)
    elif not sys.argv[1:]:
        verify_bundle()
    elif len(sys.argv) == 3 and sys.argv[1] == "--directory":
        verify_directory(Path(sys.argv[2]))
    else:
        raise SystemExit("Usage: python-ca.py [--reset | --directory EXPORTED_ROOT]")
