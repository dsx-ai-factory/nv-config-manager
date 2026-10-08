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

"""Exercise Pulse's credential handoff without accessing Vault or registries."""

import os
import subprocess
from pathlib import Path

import yaml

REPOSITORY = Path(__file__).resolve().parents[2]
PULSE_CONFIG = yaml.safe_load((REPOSITORY / ".gitlab/ci/pulse.yml").read_text())
BEFORE_SCRIPT = "\n".join(PULSE_CONFIG["pulse-scan-images"]["before_script"])
MOCK_POLICY_FETCH = """
curl() { printf '{}\n' > "$CI_PROJECT_DIR/.pulse-policy/policy.json"; }
jq() { return 0; }
"""
ASSERT_SCANNER_ENV = """
bash -c '[ "$SSA_CLIENT_ID" = "$EXPECTED_CLIENT_ID" ] &&
         [ "$SSA_CLIENT_SECRET" = "$EXPECTED_CLIENT_SECRET" ]'
"""


def run_setup(
    tmp_path: Path, credentials: dict[str, str | None]
) -> subprocess.CompletedProcess[str]:
    """Run the actual job setup with dummy Vault files and legacy variables."""
    environment = {
        "PATH": os.defpath,
        "CI_PROJECT_DIR": str(tmp_path),
        "CI_COMMIT_SHORT_SHA": "12345678",
        "CI_API_V4_URL": "https://gitlab.example.test/api/v4",
        "PULSE_IMAGE_NAME": "nv-config-manager",
        "PULSE_NSPECT_ID": "NSPECT-TEST-TEST",
        "NVCM_IMAGE_REPOSITORY": "registry.example.test/example",
        "NGC_REGISTRY_TOKEN": "placeholder-registry-token",
        "NVCM_CONTAINER_SCAN_POLICY_PROJECT": "example%2Fpolicy",
        "NVCM_CONTAINER_SCAN_POLICY_FILE": "policy.json",
        "NV_CONFIG_MANAGER_CONTAINER_SCAN_POLICY_TOKEN": "placeholder-policy-token",
        "SSA_CLIENT_ID": "legacy-placeholder-id",
        "SSA_CLIENT_SECRET": "legacy-placeholder-secret",
        "EXPECTED_CLIENT_ID": "vault-placeholder-id",
        # Shell metacharacters must survive as data, never evaluated as code.
        "EXPECTED_CLIENT_SECRET": "vault-placeholder-'\"$()`secret",
    }
    for name, value in credentials.items():
        if value is not None:
            file_path = tmp_path / name
            file_path.write_text(value)
            environment[name] = str(file_path)
    return subprocess.run(
        ["bash", "-c", MOCK_POLICY_FETCH + BEFORE_SCRIPT + ASSERT_SCANNER_ENV],
        cwd=REPOSITORY,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_vault_credentials_replace_legacy_variables(tmp_path: Path) -> None:
    result = run_setup(
        tmp_path,
        {
            "NVCM_PULSE_SSA_CLIENT_ID_FILE": "vault-placeholder-id",
            "NVCM_PULSE_SSA_CLIENT_SECRET_FILE": "vault-placeholder-'\"$()`secret",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "vault-placeholder" not in result.stdout + result.stderr


def test_missing_vault_secret_rejects_legacy_fallback(tmp_path: Path) -> None:
    result = run_setup(
        tmp_path,
        {
            "NVCM_PULSE_SSA_CLIENT_ID_FILE": "vault-placeholder-id",
            "NVCM_PULSE_SSA_CLIENT_SECRET_FILE": None,
        },
    )
    assert result.returncode != 0
    assert "Vault" in result.stderr
    assert "placeholder" not in result.stdout + result.stderr
