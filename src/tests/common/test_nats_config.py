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
"""Tests for NATS stream and subject configuration."""

from configparser import ConfigParser

import pytest

from nv_config_manager.common.config.nats import (
    _NATS_CONFIG_LOOKUPS,
    _NATS_ENUM,
    DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
    DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
    DEFAULT_CONFIG_MANAGER_NATS_STREAM,
    DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
    DEFAULT_NAUTOBOT_NATS_STREAM,
    DEFAULT_NAUTOBOT_NATS_SUBJECT,
    nats_archive_config,
    nats_dcim_change_config,
    nats_device_change_config,
    nats_nautobot_change_config,
    nats_render_change_config,
)


def _config(**options: str) -> ConfigParser:
    config = ConfigParser()
    config["nats"] = options
    return config


@pytest.mark.parametrize(
    ("config_function", "expected"),
    [
        (
            nats_render_change_config,
            (
                DEFAULT_CONFIG_MANAGER_NATS_STREAM,
                DEFAULT_CONFIG_MANAGER_RENDER_CHANGE_SUBJECT,
            ),
        ),
        (
            nats_device_change_config,
            (
                DEFAULT_CONFIG_MANAGER_NATS_STREAM,
                DEFAULT_CONFIG_MANAGER_DEVICE_CHANGE_SUBJECT,
            ),
        ),
        (
            nats_archive_config,
            (
                DEFAULT_CONFIG_MANAGER_NATS_STREAM,
                DEFAULT_CONFIG_MANAGER_ARCHIVE_SUBJECT,
            ),
        ),
        (
            nats_dcim_change_config,
            (DEFAULT_NAUTOBOT_NATS_STREAM, DEFAULT_NAUTOBOT_NATS_SUBJECT),
        ),
        (
            nats_nautobot_change_config,
            (DEFAULT_NAUTOBOT_NATS_STREAM, DEFAULT_NAUTOBOT_NATS_SUBJECT),
        ),
    ],
)
def test_nats_change_config_defaults(config_function, expected):
    assert config_function(_config()) == expected


@pytest.mark.parametrize(
    ("config_function", "option_prefix"),
    [
        (nats_render_change_config, "render_change"),
        (nats_device_change_config, "device_change"),
        (nats_archive_config, "archive"),
        (nats_dcim_change_config, "dcim_change"),
        (nats_nautobot_change_config, "nautobot"),
    ],
)
def test_nats_change_config_explicit_overrides(config_function, option_prefix):
    config = _config(
        **{
            f"{option_prefix}_stream": "custom-stream",
            f"{option_prefix}_subject": "custom.subject",
        }
    )

    assert config_function(config) == ("custom-stream", "custom.subject")


@pytest.mark.parametrize(
    "config_function",
    [
        nats_render_change_config,
        nats_device_change_config,
        nats_archive_config,
    ],
)
def test_config_manager_events_use_shared_stream_fallback(config_function):
    assert config_function(_config(config_manager_stream="shared-stream"))[0] == "shared-stream"


def test_dcim_change_uses_legacy_nautobot_fallbacks():
    config = _config(
        nautobot_stream="legacy-stream",
        nautobot_subject="legacy.subject",
    )

    assert nats_dcim_change_config(config) == ("legacy-stream", "legacy.subject")


def test_dcim_change_explicit_config_takes_precedence_over_legacy_fallbacks():
    config = _config(
        dcim_change_stream="dcim-stream",
        dcim_change_subject="dcim.subject",
        nautobot_stream="legacy-stream",
        nautobot_subject="legacy.subject",
    )

    assert nats_dcim_change_config(config) == ("dcim-stream", "dcim.subject")


def test_every_nats_config_option_has_lookup_metadata():
    assert set(_NATS_CONFIG_LOOKUPS) == set(_NATS_ENUM)
