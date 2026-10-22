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


def _exported_store(directory: Path) -> tuple[dict, Path, str]:
    """Create a valid bundle with a separate synthetic distrusted anchor."""
    checker = runpy.run_path(str(_CHECKER))
    certificates = re.findall(
        r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",
        Path(certifi.where()).read_text(),
        flags=re.DOTALL,
    )
    suspect = certificates.pop()
    # Use a real certificate as a synthetic distrusted root; no network fixture.
    checker["BLOCKED_ROOTS"].add(hashlib.sha256(ssl.PEM_cert_to_DER_cert(suspect)).hexdigest())
    bundle = directory / "etc/ssl/certs/ca-certificates.crt"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("\n".join(certificates))
    return checker, bundle, suspect


@pytest.mark.parametrize("location", ["bundle", "individual", "local", "hashed", "clean"])
def test_exported_ca_store(tmp_path: Path, location: str) -> None:
    """Reject affected roots in bundles, source directories and hashed CA paths."""
    checker, bundle, suspect = _exported_store(tmp_path)
    if location == "bundle":
        bundle.write_text(bundle.read_text() + "\n" + suspect)
    elif location in {"individual", "local"}:
        source = "usr/local/share" if location == "local" else "usr/share"
        anchor = tmp_path / source / "ca-certificates/stale.crt"
        anchor.parent.mkdir(parents=True)
        anchor.write_text(suspect)
        # docker cp retains absolute symlinks. Audit their exported targets too.
        (bundle.parent / "stale.pem").symlink_to(f"/{source}/ca-certificates/stale.crt")
    elif location == "hashed":
        (bundle.parent / "deadbeef.0").write_text(suspect)

    if location == "clean":
        checker["verify_directory"](tmp_path)
    else:
        with pytest.raises(SystemExit, match="Affected emSign trust anchor remains"):
            checker["verify_directory"](tmp_path)


def test_missing_exported_bundle_fails(tmp_path: Path) -> None:
    """A missing exported bundle must fail the audit."""
    checker = runpy.run_path(str(_CHECKER))
    with pytest.raises(SystemExit, match="target missing from image export"):
        checker["verify_directory"](tmp_path)


@pytest.mark.parametrize("absolute", [False, True])
def test_bundle_link_resolves_inside_image(tmp_path: Path, absolute: bool) -> None:
    """Accept relative and absolute bundle links whose targets were exported."""
    checker, bundle, _ = _exported_store(tmp_path)
    target = tmp_path / "usr/local/share/ca-certificates/bundle.crt"
    target.parent.mkdir(parents=True)
    bundle.rename(target)
    bundle.symlink_to(
        "/usr/local/share/ca-certificates/bundle.crt"
        if absolute
        else "../../../usr/local/share/ca-certificates/bundle.crt"
    )
    checker["verify_directory"](tmp_path)


@pytest.mark.parametrize("absolute", [False, True])
def test_bundle_link_cannot_read_host_store(tmp_path: Path, absolute: bool) -> None:
    """Do not approve an image by reading a populated host trust store."""
    directory = tmp_path / "image"
    checker, bundle, _ = _exported_store(directory)
    host_bundle = tmp_path / "host-bundle.crt"
    bundle.rename(host_bundle)
    bundle.symlink_to(host_bundle if absolute else "../../../../host-bundle.crt")
    with pytest.raises(SystemExit, match="image export"):
        checker["verify_directory"](directory)


@pytest.mark.parametrize("cycle", [False, True])
def test_unresolved_ca_links_fail(tmp_path: Path, cycle: bool) -> None:
    """Do not skip missing CA targets or looping symlinks."""
    checker, bundle, _ = _exported_store(tmp_path)
    link = bundle.parent / "deadbeef.0"
    link.symlink_to("other.pem")
    if cycle:
        link.with_name("other.pem").symlink_to(link.name)
    with pytest.raises(SystemExit, match="image export"):
        checker["verify_directory"](tmp_path)


@pytest.mark.parametrize("contents", ["abcde", "bm90IGEgY2VydGlmaWNhdGU=", "\u00e9"])
def test_malformed_individual_certificate_fails(tmp_path: Path, contents: str) -> None:
    """Reject malformed PEM and DER data with a readable audit error."""
    checker, bundle, _ = _exported_store(tmp_path)
    (bundle.parent / "malformed.pem").write_text(
        f"-----BEGIN CERTIFICATE-----\n{contents}\n-----END CERTIFICATE-----"
    )
    with pytest.raises(SystemExit, match="Invalid PEM certificate"):
        checker["verify_directory"](tmp_path)


def test_malformed_bundle_fails(tmp_path: Path) -> None:
    """Report an invalid combined bundle without exposing an SSL traceback."""
    checker = runpy.run_path(str(_CHECKER))
    bundle = tmp_path / "bundle.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\nabcde\n-----END CERTIFICATE-----")
    with pytest.raises(SystemExit, match="Invalid CA bundle"):
        checker["verify_bundle"](str(bundle))
