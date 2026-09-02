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
"""Scheduled device certificate rotation workflow."""

import asyncio
from datetime import timedelta

from pydantic import BaseModel, Field
from temporalio import workflow
from temporalio.common import RetryPolicy

from nv_config_manager.temporal.common.decorators.workflow import run_nv_config_manager_workflow
from nv_config_manager.temporal.common.mixins.metadata import WorkflowMetadataMixin
from nv_config_manager.temporal.common.workflow_references import DeviceReference

with workflow.unsafe.imports_passed_through():
    from nv_config_manager.temporal.common.mixins.device import DeviceMixin
    from nv_config_manager.temporal.ngc.activities.certificate_rotation import (
        RotateDeviceCertificatesInput,
        rotate_device_certificates,
    )
    from nv_config_manager.temporal.ngc.activities.dcim import (
        GetNetworkDeviceInput,
        GetZTPDeviceInput,
        get_network_device,
        get_ztp_device,
    )


class CertificateRotationInput(BaseModel):
    """One device selected by the nightly schedule reconciler."""

    device_id: DeviceReference = Field(description="Device whose certificates are reissued.")


@workflow.defn
class CertificateRotationWorkflow(WorkflowMetadataMixin, DeviceMixin):
    """Reissue all assigned certificates and replace their existing NVUE IDs."""

    workflow_name = "Certificate Rotation"
    workflow_description = "Reissue and replace device certificates from configured PKI sources"
    workflow_input_class = CertificateRotationInput
    workflow_api_enabled = False
    workflow_namespace = "ngc"

    @run_nv_config_manager_workflow
    async def run(self, workflow_input: CertificateRotationInput) -> tuple[str, ...]:
        """Load fresh DCIM intent and rotate every certificate assigned to the device."""
        retry_policy = RetryPolicy(maximum_attempts=3)
        network_result, ztp_result = await asyncio.gather(
            workflow.execute_activity(
                get_network_device,
                GetNetworkDeviceInput(device_id=workflow_input.device_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=retry_policy,
            ),
            workflow.execute_activity(
                get_ztp_device,
                GetZTPDeviceInput(device_id=workflow_input.device_id),
                start_to_close_timeout=timedelta(minutes=1),
                retry_policy=retry_policy,
            ),
        )
        DeviceMixin.attach_device_search_attributes(network_result.device)
        result = await workflow.execute_activity(
            rotate_device_certificates,
            RotateDeviceCertificatesInput(
                device_data=network_result.device,
                ztp_device=ztp_result.device,
            ),
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=retry_policy,
        )
        return result.certificate_ids
