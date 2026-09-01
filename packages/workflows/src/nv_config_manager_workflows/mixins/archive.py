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
"""Mixin to archive workflow results without depending on the NVCM service."""

from __future__ import annotations

from datetime import timedelta

from pydantic import BaseModel
from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributes

from nv_config_manager_workflows.mixins.base import BaseMixin

PUBLISH_NATS_ACTIVITY_NAME = "publish_nats"


class WorkflowResultLog(BaseModel):
    """Workflow result data sent to the configured archive."""

    workflow_id: str
    workflow_type: str
    start_time: str
    publish_time: str
    search_attributes: SearchAttributes

    @staticmethod
    def from_workflow_info(info: workflow.Info) -> WorkflowResultLog:
        """Build a result from workflow info."""
        return WorkflowResultLog(
            workflow_id=info.workflow_id,
            workflow_type=info.workflow_type,
            start_time=info.start_time.isoformat(),
            publish_time=workflow.now().isoformat(),
            search_attributes=info.search_attributes,
        )


class ArchiveMixin(BaseMixin):
    """Mixin to send a workflow result to NATS for archival."""

    async def archive_results(self) -> None:
        """Publish the workflow result through the registered NATS activity."""
        await workflow.execute_activity(
            PUBLISH_NATS_ACTIVITY_NAME,
            {
                "subject": None,
                "message": WorkflowResultLog.from_workflow_info(workflow.info()).model_dump_json(),
            },
            schedule_to_close_timeout=timedelta(minutes=1),
            retry_policy=RetryPolicy(maximum_attempts=1),
        )
