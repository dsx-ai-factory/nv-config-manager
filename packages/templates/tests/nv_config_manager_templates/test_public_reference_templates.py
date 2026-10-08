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
"""Tests for public reference template role trees."""

from __future__ import annotations

from pathlib import Path

TEMPLATE_ROOT = (
    Path(__file__).resolve().parents[2] / "src" / "nv_config_manager_templates" / "templates"
)

EXPECTED_MLNX_ENTRYPOINTS = {
    "mlnx-os/ib-core/3.12.2002/entrypoint/full-config.j2",
    "mlnx-os/ib-core/3.12.4002/entrypoint/full-config.j2",
    "mlnx-os/ib-core/3.12.5000/entrypoint/full-config.j2",
    "mlnx-os/ib-core/3.12.6000/entrypoint/full-config.j2",
    "mlnx-os/ib-leaf/3.11.2016/entrypoint/full-config.j2",
    "mlnx-os/ib-leaf/3.12.2002/entrypoint/full-config.j2",
    "mlnx-os/ib-leaf/3.12.4002/entrypoint/full-config.j2",
    "mlnx-os/ib-leaf/3.12.5000/entrypoint/full-config.j2",
    "mlnx-os/ib-leaf/3.12.6000/entrypoint/full-config.j2",
    "mlnx-os/ib-spine/3.12.2002/entrypoint/full-config.j2",
    "mlnx-os/ib-spine/3.12.4002/entrypoint/full-config.j2",
    "mlnx-os/ib-spine/3.12.5000/entrypoint/full-config.j2",
    "mlnx-os/ib-spine/3.12.6000/entrypoint/full-config.j2",
}

EXPECTED_NVSWITCH_ENTRYPOINTS = {
    "fm-config.cfg.j2",
    "nmx-commands.txt.j2",
    "pre-provisioning-commands.txt.j2",
    "provisioning-complete.sh.j2",
    "startup.yaml.j2",
    "ztp.json.j2",
}


def test_mlnx_os_ib_reference_templates_are_present() -> None:
    entrypoints = {
        str(path.relative_to(TEMPLATE_ROOT))
        for path in (TEMPLATE_ROOT / "mlnx-os").glob("ib-*/**/entrypoint/*.j2")
    }

    assert entrypoints == EXPECTED_MLNX_ENTRYPOINTS


def test_mlnx_os_role_common_uses_current_interface_dataclass() -> None:
    template = TEMPLATE_ROOT / "mlnx-os/role-common/base/full-config.j2"

    assert '(device_data|interface_by_name("eth0")).primary_ipv4.split("/")' in (
        template.read_text(encoding="utf-8")
    )


def test_nv_os_nvswitch_reference_templates_are_present() -> None:
    for version in ("25.02.2134", "25.02.2342", "25.02.2344", "25.02.2445"):
        entrypoint_dir = TEMPLATE_ROOT / "nv-os/nvswitch" / version / "entrypoint"
        entrypoints = {path.name for path in entrypoint_dir.glob("*.j2")}

        assert EXPECTED_NVSWITCH_ENTRYPOINTS - entrypoints <= {"fwupdate-commands.txt.j2"}

    assert (TEMPLATE_ROOT / "nv-os/role-common/base/ztp.json.j2").is_file()
    assert (TEMPLATE_ROOT / "nv-os/nvswitch/base/ztp.json.j2").is_file()
