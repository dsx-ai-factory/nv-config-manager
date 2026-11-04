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
"""Replace and audit CA stores in the exact filesystem copied into a final image."""

import runpy
import shutil
import sys
import tempfile
from pathlib import Path

CHECKER = runpy.run_path(str(Path(__file__).with_name("python-ca.py")))
# crypto/x509/root_linux.go: remove every default Linux bundle and directory.
TRUST_PATHS = (
    "etc/ssl/certs",
    "etc/pki/tls/certs",
    "etc/ssl/ca-bundle.pem",
    "etc/pki/tls/cacert.pem",
    "etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "etc/ssl/cert.pem",
    "usr/share/ca-certificates",
    "usr/local/share/ca-certificates",
)
FRESH_DIRECTORIES = ("etc/ssl/certs", "usr/share/ca-certificates")


def image_path(root: Path, relative: str) -> Path:
    """Resolve image parents safely while preserving a final symlink for removal."""
    path = Path(relative)
    parent = CHECKER["exported_path"](root, path.parent, allow_missing=True)
    return parent / path.name


def refresh(root: Path, source: Path) -> None:
    """Discard inherited trust and verify the final runtime stores before export."""
    root, source = root.resolve(), source.resolve()
    if root == source or root == Path("/"):
        raise ValueError("Runtime root must be separate from the source trust store")
    for relative in TRUST_PATHS:
        path = image_path(root, relative)
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    for relative in FRESH_DIRECTORIES:
        shutil.copytree(source / relative, image_path(root, relative), symlinks=True)
    # Audit files taken from the assembled runtime, not from the Debian stage.
    # The final FROM scratch stage copies this filesystem without further edits.
    with tempfile.TemporaryDirectory() as temporary:
        exported = Path(temporary)
        for relative in FRESH_DIRECTORIES:
            shutil.copytree(image_path(root, relative), exported / relative, symlinks=True)
        CHECKER["verify_directory"](exported)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: refresh-rootfs-ca.py RUNTIME_ROOT")
    refresh(Path(sys.argv[1]), Path("/"))
