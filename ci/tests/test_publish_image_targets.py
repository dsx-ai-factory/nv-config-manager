# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Exercise release aliases with mocked registry operations."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "version,tags,aliases,success,mismatch",
    [
        ("1.4.10", "1.4.9\n1.4.10\n1.4.11-rc.1", {"latest", "1.4-latest"}, True, False),
        ("1.3.2", "1.3.2\n1.4.0\n1.30.0", {"1.3-latest"}, True, False),
        ("1.3.1", "1.3.1\n1.3.2\n1.4.0", set(), True, False),
        ("1.4.1-rc.1", "1.4.0\n1.4.1-rc.1", set(), False, False),
        ("1.4.0", "", set(), False, False),
        ("1.4.0", "1.4.0", set(), False, True),
    ],
)
def test_release_aliases(
    tmp_path: Path, version: str, tags: str, aliases: set[str], success: bool, mismatch: bool
) -> None:
    """Only eligible stable aliases move, after all destination digests match."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    bash = shutil.which("bash")
    assert bash
    (bin_dir / "git").write_text('#!/bin/sh\nprintf "%s\\n" "$TEST_TAGS"\n')
    (bin_dir / "skopeo").write_text(
        f"#!{bash}\n"
        'printf "%s\\n" "$*" >> "$TEST_LOG"\n'
        'case "$1" in\n'
        "login) cat >/dev/null ;;\n"
        "inspect)\n"
        '  if [[ "$TEST_MISMATCH" == 1 && "${@: -1}" == *mirror*oidc-proxy* ]]; then\n'
        '    printf "sha256:%064d\\n" 2\n'
        '  else printf "sha256:%064d\\n" 1; fi ;;\n'
        "copy)\n"
        '  while [[ "$1" != --digestfile ]]; do shift; done\n'
        '  printf "sha256:%064d\\n" 1 > "$2" ;;\n'
        "esac\n"
    )
    for executable in bin_dir.iterdir():
        executable.chmod(0o755)
    log = tmp_path / "registry.log"
    result = subprocess.run(
        [bash, str(ROOT / ".gitlab/ci/scripts/publish_image_targets.sh"), version, "--latest"],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "TEST_TAGS": tags,
            "TEST_LOG": str(log),
            "TEST_MISMATCH": str(int(mismatch)),
            "NVCM_IMAGE_TARGETS": (
                "source|registry.invalid/source|TEST_TOKEN|\n"
                "mirror|registry.invalid/mirror|TEST_TOKEN|"
            ),
            "NVCM_IMAGE_TARGET": "source",
            "TEST_TOKEN": "test-placeholder",
        },
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) == success, result.stderr
    operations = log.read_text().splitlines() if log.exists() else []
    copies = [line for line in operations if line.startswith("copy ")]
    assert {line.rsplit(":", 1)[-1] for line in copies} == aliases
    assert len(copies) == 10 * 2 * len(aliases)
    if aliases:
        first_copy = next(i for i, line in enumerate(operations) if line.startswith("copy "))
        assert sum(line.startswith("inspect ") for line in operations[:first_copy]) == 30
        for repository in ("source", "mirror"):
            assert sum(
                f"registry.invalid/{repository}/" in line.split()[-1] for line in copies
            ) == (10 * len(aliases))
