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
"""Tests for DSX Air simulation cloud-init generation."""

from __future__ import annotations

from pathlib import Path

import yaml

from nv_config_manager_installer.air_sim.cloud_init import generate_server_cloud_init

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_generated_minimal_server_cloud_init_matches_golden_output() -> None:
    user_data = generate_server_cloud_init(
        internal_mac="44:38:39:00:00:01",
        oob_ssh_password="test-oob-password",
        internal_ip="10.100.1.2/25",
        site_name="SPO01",
        oob_gateway="10.100.1.1",
    )

    assert user_data == (FIXTURES_DIR / "minimal_server_cloud_init.yaml").read_text()


def test_git_token_is_not_persisted_in_setup_script() -> None:
    token = "ghp_private_token"
    user_data = generate_server_cloud_init(
        internal_mac="44:38:39:00:00:01",
        oob_ssh_password="test-oob-password",
        internal_ip="10.100.1.2/25",
        site_name="SPO01",
        oob_gateway="10.100.1.1",
        config_manager_repo="https://github.com/NVIDIA/nv-config-manager.git",
        config_manager_ref="main",
        git_token=token,
    )

    cloud_config = yaml.safe_load(user_data.removeprefix("#cloud-config\n"))
    write_files = {entry["path"]: entry for entry in cloud_config["write_files"]}
    setup_script = write_files["/opt/nvcm-setup.sh"]
    git_token_file = write_files["/opt/nvcm-git-token"]

    assert setup_script["permissions"] == "0700"
    assert setup_script["owner"] == "root:root"
    assert token not in setup_script["content"]
    assert "credential.helper" in setup_script["content"]
    assert "https://x-access-token:" not in setup_script["content"]
    assert git_token_file["permissions"] == "0600"
    assert git_token_file["owner"] == "root:root"
    assert git_token_file["content"] == token


def test_setup_completion_marker_follows_cluster_status_check() -> None:
    user_data = generate_server_cloud_init(
        internal_mac="44:38:39:00:00:01",
        oob_ssh_password="test-oob-password",
        internal_ip="10.100.1.2/25",
        site_name="SPO01",
        oob_gateway="10.100.1.1",
        config_manager_repo="https://github.com/NVIDIA/nv-config-manager.git",
        config_manager_ref="main",
    )

    cloud_config = yaml.safe_load(user_data.removeprefix("#cloud-config\n"))
    write_files = {entry["path"]: entry for entry in cloud_config["write_files"]}
    setup_script = write_files["/opt/nvcm-setup.sh"]["content"]

    assert setup_script.index("kubectl get nodes -o wide") < setup_script.index(
        "NVCM DSX Air Setup Complete!"
    )
