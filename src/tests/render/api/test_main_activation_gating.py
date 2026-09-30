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
"""Verifies the activation router is only mounted in the FastAPI app when
both unsafe_dynamic_template_plugins_enabled and
..._acknowledge_arbitrary_code_execution are true (SRD FR-241/FR-245).
Does NOT check HTTP status codes for this -- render-api has a global auth
middleware that returns 403 for any unauthenticated request regardless of
whether the path exists, so "does this route exist" has to be checked via
route introspection, not by POSTing and reading the status code.
"""

import importlib

import pytest

from nv_config_manager.render.api import main


@pytest.fixture(autouse=True)
def _restore_main_module_after_test(custom_ini):
    """Every test here reloads the shared `main` module in place (mutating
    sys.modules, not just a local binding) -- restore it to the flag-off
    state afterward so any other test importing `main` fresh later in the
    same process doesn't inherit a mutated app."""
    yield
    custom_ini(
        """
        [render]
        api_service = http://render-api.example.local:9000
        """
    )
    importlib.reload(main)


def _activation_route_mounted(app) -> bool:
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            if any("/activation" in sub.path for sub in route.original_router.routes):
                return True
    return False


def test_activation_route_absent_by_default(custom_ini):
    custom_ini(
        """
        [render]
        api_service = http://render-api.example.local:9000
        """
    )
    importlib.reload(main)

    assert not _activation_route_mounted(main.app)


def test_activation_route_absent_when_only_enabled_is_true(custom_ini):
    """Both flags are required -- enabled alone is not enough (FR-241:
    'both values required to expose the upload and activation APIs')."""
    custom_ini(
        """
        [render]
        unsafe_dynamic_template_plugins_enabled = true
        """
    )
    importlib.reload(main)

    assert not _activation_route_mounted(main.app)


def test_activation_route_absent_when_only_acknowledgement_is_true(custom_ini):
    custom_ini(
        """
        [render]
        unsafe_dynamic_template_plugins_acknowledge_arbitrary_code_execution = true
        """
    )
    importlib.reload(main)

    assert not _activation_route_mounted(main.app)


def test_activation_route_present_when_both_flags_true(custom_ini):
    custom_ini(
        """
        [render]
        unsafe_dynamic_template_plugins_enabled = true
        unsafe_dynamic_template_plugins_acknowledge_arbitrary_code_execution = true
        """
    )
    importlib.reload(main)

    assert _activation_route_mounted(main.app)
