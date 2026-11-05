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
"""Run the promotion resolver against real Git histories without publishing tags."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "source_branch,matching_branch,accepted",
    [
        ("main", False, True),
        ("release/1.4.2", False, True),
        ("feature/unreviewed", False, False),
        ("release/1.4.3", False, False),
        ("feature/unreviewed", True, False),
    ],
)
def test_promotion_requires_trusted_ancestry(
    tmp_path: Path, source_branch: str, matching_branch: bool, accepted: bool
) -> None:
    """An RC needs main or its own release branch, even when both RC tags exist."""
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args: str) -> str:
        return subprocess.check_output(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=repo, text=True
        ).strip()

    git("init", "--quiet", "-b", "main")
    git("config", "user.name", "Release Test")
    git("config", "user.email", "release-test@example.invalid")
    (repo / "CHANGELOG.md").write_text("# Changelog\n\n## 1.4.2\n\n### Fixed\n\n- Fix.\n")
    git("add", "CHANGELOG.md")
    git("commit", "--quiet", "-m", "Base")
    if matching_branch:
        git("branch", "release/1.4.2")
    if source_branch != "main":
        git("switch", "--quiet", "-c", source_branch)
    (repo / "candidate").write_text("release candidate")
    git("add", "candidate")
    git("commit", "--quiet", "-m", "Candidate")
    candidate = git("rev-parse", "HEAD")
    git("tag", "1.4.2-rc.1")
    git("tag", "bindings/go/v1.4.2-rc.1")
    # An approved branch may advance after tagging; the RC need not be its tip.
    git("commit", "--quiet", "--allow-empty", "-m", "Next change")
    origin = tmp_path / "origin.git"
    git("init", "--quiet", "--bare", str(origin))
    git("remote", "add", "origin", str(origin))
    git("push", "--quiet", "origin", "--all")
    git("push", "--quiet", "origin", "--tags")
    scripts = repo / ".github/scripts"
    scripts.mkdir(parents=True)
    shutil.copy2(ROOT / ".github/scripts/extract-release-notes", scripts)
    workflow = yaml.safe_load((ROOT / ".github/workflows/promote-release.yml").read_text())
    resolver = next(s for s in workflow["jobs"]["validate"]["steps"] if s.get("id") == "resolve")
    output = tmp_path / "output"
    result = subprocess.run(
        ["bash", "-c", resolver["run"]],
        cwd=repo,
        env={
            **os.environ,
            "DEFAULT_BRANCH": "main",
            "SOURCE_RC_TAG": "1.4.2-rc.1",
            "INPUT_TARGET_TAG": "",
            "GITHUB_OUTPUT": str(output),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert (result.returncode == 0) is accepted, result.stdout + result.stderr
    if accepted:
        assert f"commit={candidate}\n" in output.read_text()
    else:
        assert "RC commit must be reachable" in result.stdout
        assert not output.exists()
