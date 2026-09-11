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
"""Tests for the declarative workflow lock: spec validation and key building."""

import pytest
from pydantic import BaseModel, ValidationError

from nv_config_manager_workflows.metadata import (
    WorkflowLockSpec,
    WorkflowMetadataMixin,
    build_workflow_lock_key,
)


class LockInput(BaseModel):
    site: str
    device: str | None = None


class TestWorkflowLockSpec:
    """The spec refuses configurations where the lock would lapse mid-run."""

    def test_defaults_wait_and_renew_before_expiry(self) -> None:
        spec = WorkflowLockSpec(key_fields=["site"])

        assert spec.on_conflict == "wait"
        assert spec.renew_interval_seconds < spec.ttl_seconds

    def test_at_least_one_key_field_is_required(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowLockSpec(key_fields=[])

    def test_a_renewal_that_starts_after_expiry_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="renew_interval_seconds must be less"):
            WorkflowLockSpec(key_fields=["site"], ttl_seconds=30, renew_interval_seconds=30)

    def test_a_non_positive_wait_timeout_is_rejected(self) -> None:
        with pytest.raises(ValidationError, match="wait_timeout_seconds must be positive"):
            WorkflowLockSpec(key_fields=["site"], wait_timeout_seconds=0)

    def test_a_renewal_that_leaves_no_buffer_is_rejected(self) -> None:
        """Renewal must finish before the TTL lapses, not merely precede it."""
        with pytest.raises(ValidationError, match="renewal buffer"):
            WorkflowLockSpec(key_fields=["site"], ttl_seconds=60, renew_interval_seconds=50)


class TestBuildWorkflowLockKey:
    """Keys are derived deterministically; running workers hold the old format."""

    def test_the_key_uses_the_declared_scope_and_input_fields(self) -> None:
        spec = WorkflowLockSpec(
            key_fields=["site", "device"],
            namespace="ngc",
            include_workflow_name=True,
        )

        assert (
            build_workflow_lock_key(
                spec,
                workflow_name="DeployWorkflow",
                namespace="ignored",
                workflow_input=LockInput(site="rdu", device="leaf-01"),
            )
            == "wf-lock:ngc:DeployWorkflow:site=rdu:device=leaf-01"
        )

    def test_the_workflow_name_is_omitted_unless_opted_in(self) -> None:
        """include_workflow_name is off by default, so one key spans a workflow family."""
        spec = WorkflowLockSpec(key_fields=["site"], namespace="ngc")

        assert (
            build_workflow_lock_key(
                spec,
                workflow_name="DeployWorkflow",
                namespace=None,
                workflow_input=LockInput(site="rdu"),
            )
            == "wf-lock:ngc:site=rdu"
        )

    def test_the_legacy_unencoded_format_is_retained(self) -> None:
        spec = WorkflowLockSpec(
            key_fields=["site"],
            namespace="fabric:one",
            include_workflow_name=True,
        )

        assert (
            build_workflow_lock_key(
                spec,
                workflow_name="Deploy:Workflow",
                namespace=None,
                workflow_input=LockInput(site="rdu/device 1"),
            )
            == "wf-lock:fabric:one:Deploy:Workflow:site=rdu/device 1"
        )

    @pytest.mark.parametrize(
        ("site", "device", "rejected"),
        [
            ("rdu", None, "device"),
            ("rdu", "", "device"),
            ("", "leaf-01", "site"),
        ],
        ids=["trailing-field-absent", "trailing-field-empty", "leading-field-empty"],
    )
    def test_a_missing_input_value_is_rejected(
        self, site: str, device: str | None, rejected: str
    ) -> None:
        """An absent or empty value would collapse two resources onto one key.

        Every declared field is checked, not just the first: the key is only
        unique if all of them are present.
        """
        spec = WorkflowLockSpec(key_fields=["site", "device"])

        with pytest.raises(ValueError, match=f"{rejected}.*missing or empty"):
            build_workflow_lock_key(
                spec,
                workflow_name="DeployWorkflow",
                namespace=None,
                workflow_input=LockInput(site=site, device=device),
            )


class TestMetadataMixin:
    """Workflows opt into locking by declaring a spec on the class."""

    def test_a_declared_spec_is_returned(self) -> None:
        spec = WorkflowLockSpec(key_fields=["site"])

        class Locked(WorkflowMetadataMixin):
            workflow_lock = spec

        assert Locked.get_workflow_lock() is spec

    def test_no_lock_is_reported_by_default(self) -> None:
        class Unlocked(WorkflowMetadataMixin):
            pass

        assert Unlocked.get_workflow_lock() is None
