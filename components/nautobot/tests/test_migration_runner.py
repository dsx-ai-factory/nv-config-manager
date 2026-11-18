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
"""Tests for dedicated Nautobot MCP inventory token provisioning."""

from __future__ import annotations

import runpy
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

RUNNER = Path(__file__).resolve().parents[1] / "nv_config_manager_jobs/migration_runner.py"


@pytest.fixture
def runner(monkeypatch):
    django = MagicMock()
    registry = MagicMock()
    database = MagicMock()
    auth = MagicMock()
    monkeypatch.setitem(sys.modules, "django", django)
    monkeypatch.setitem(sys.modules, "django.apps", MagicMock(apps=registry))
    monkeypatch.setitem(sys.modules, "django.db", database)
    monkeypatch.setitem(sys.modules, "django.contrib.auth", auth)
    models = {name: MagicMock() for name in ["Token", "ObjectPermission", "ContentType"]}
    registry.get_model.side_effect = lambda app, name: models[name]
    user = MagicMock(is_superuser=False, is_staff=False)
    auth.get_user_model.return_value.objects.get_or_create.return_value = (user, True)
    token = MagicMock(user_id=user.pk)
    models["Token"].objects.get_or_create.return_value = (token, True)
    permission = MagicMock()
    models["ObjectPermission"].objects.update_or_create.return_value = (permission, True)
    models["ContentType"].objects.filter.side_effect = lambda **kw: [kw]
    monkeypatch.setenv("NAUTOBOT_READ_ONLY_API_TOKEN", "a" * 40)
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "nautobot.core.settings")
    module = runpy.run_path(str(RUNNER))
    return module["create_read_only_api_token"], user, token, permission, models


def test_read_only_token_has_only_inventory_view_access_and_reconciles(runner):
    create, user, token, permission, models = runner
    create()
    create()
    user.set_unusable_password.assert_called()
    user.groups.clear.assert_called()
    user.user_permissions.clear.assert_called()
    user.object_permissions.set.assert_called_with([permission])
    defaults = models["ObjectPermission"].objects.update_or_create.call_args.kwargs["defaults"]
    assert defaults == {"actions": ["view"], "constraints": {}, "enabled": True}
    permission.object_types.set.assert_called_with(
        [
            {"app_label__in": ["dcim", "ipam", "circuits", "tenancy", "virtualization"]},
            {"app_label": "extras", "model__in": ["status", "role", "tag", "customfield", "customfieldchoice"]},
        ]
    )
    permission.users.set.assert_called_with([user])
    permission.groups.clear.assert_called()
    assert token.write_enabled is False
    assert token.expires is None
    assert models["Token"].objects.get_or_create.call_args.kwargs["defaults"]["write_enabled"] is False
    models["Token"].objects.filter.assert_called_with(user=user)
    models["Token"].objects.filter.return_value.exclude.assert_called_with(pk=token.pk)
    models["Token"].objects.filter.return_value.exclude.return_value.delete.assert_called()


def test_absent_token_skips_provisioning(runner, monkeypatch):
    create, user, token, permission, models = runner
    monkeypatch.delenv("NAUTOBOT_READ_ONLY_API_TOKEN")
    create()
    user.save.assert_not_called()
    models["Token"].objects.get_or_create.assert_not_called()


@pytest.mark.parametrize("value", ["too-short", "g" * 40])
def test_malformed_token_is_rejected_without_exposing_it(runner, monkeypatch, value):
    create, user, token, permission, models = runner
    monkeypatch.setenv("NAUTOBOT_READ_ONLY_API_TOKEN", value)
    with pytest.raises(ValueError, match="40 lowercase hexadecimal") as error:
        create()
    assert value not in str(error.value)
    user.save.assert_not_called()


@pytest.mark.parametrize("flag", ["is_superuser", "is_staff"])
def test_privileged_account_is_rejected(runner, flag):
    create, user, token, permission, models = runner
    setattr(user, flag, True)
    with pytest.raises(ValueError, match="must not be a superuser or staff"):
        create()
    models["Token"].objects.get_or_create.assert_not_called()


def test_token_owned_by_another_user_is_not_reassigned(runner):
    create, user, token, permission, models = runner
    token.user_id = "another-user"
    with pytest.raises(ValueError, match="already belongs to another"):
        create()
    token.save.assert_not_called()
    models["Token"].objects.filter.assert_not_called()
