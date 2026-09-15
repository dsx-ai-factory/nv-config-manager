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
"""Integration tests for Nautobot deployment.

These tests verify that the Nautobot server pod is running with the expected
configuration, including git token environment variables injected via Helm.
"""

import json
import subprocess

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.dcim_provider("nautobot-2x")]


def _get_nautobot_pod(namespace: str) -> str | None:
    """Return the name of the first running Nautobot server pod."""
    result = subprocess.run(
        [
            "kubectl",
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/name=nv-config-manager-nautobot",
            "-o",
            "json",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pods = json.loads(result.stdout)
    for pod in pods.get("items", []):
        phase = pod.get("status", {}).get("phase", "")
        if phase == "Running":
            return pod["metadata"]["name"]
    return None


def _pod_env(namespace: str, pod: str, container: str, var: str) -> str | None:
    """Read a single environment variable from a running container.

    Uses ``python3`` instead of ``printenv`` because the Nautobot container is
    built on a distroless base image that lacks coreutils.
    """
    result = subprocess.run(
        [
            "kubectl",
            "exec",
            "-n",
            namespace,
            pod,
            "-c",
            container,
            "--",
            "python3",
            "-c",
            f"import os,sys;v=os.environ.get({var!r});print(v,end='') if v is not None else sys.exit(1)",
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return None
    return result.stdout


@pytest.mark.ci_only
class TestNautobotGitTokens:
    """Verify git-token env vars are injected into the Nautobot server pod.

    The CI pipeline passes ``--git-token testgit dummy-token-value`` and
    the `testgit` git token entry in the installer config, which creates a
    K8s secret and generates Helm values under
    ``secrets.vault.paths.gitTokens``.  The Helm template translates each
    entry into ``GIT_TOKEN_<NAME>`` and (optionally) ``GIT_USERNAME_<NAME>``
    environment variables on the nautobot container.
    """

    @pytest.fixture()
    def nautobot_pod(self, config_manager_namespace: str) -> str:
        pod = _get_nautobot_pod(config_manager_namespace)
        if pod is None:
            pytest.skip("No running Nautobot server pod found")
        return pod

    @pytest.mark.timeout(30)
    def test_git_token_env_var(
        self,
        config_manager_namespace: str,
        nautobot_pod: str,
    ) -> None:
        """GIT_TOKEN_TESTGIT should contain the dummy token value."""
        val = _pod_env(config_manager_namespace, nautobot_pod, "nautobot", "GIT_TOKEN_TESTGIT")
        assert val == "dummy-token-value", (
            f"Expected GIT_TOKEN_TESTGIT='dummy-token-value', got {val!r}"
        )

    @pytest.mark.timeout(30)
    def test_git_username_env_var(
        self,
        config_manager_namespace: str,
        nautobot_pod: str,
    ) -> None:
        """GIT_USERNAME_TESTGIT should contain the dummy username."""
        val = _pod_env(config_manager_namespace, nautobot_pod, "nautobot", "GIT_USERNAME_TESTGIT")
        assert val == "dummy-user", f"Expected GIT_USERNAME_TESTGIT='dummy-user', got {val!r}"
