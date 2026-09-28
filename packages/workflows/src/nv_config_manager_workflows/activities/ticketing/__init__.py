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
"""Ticketing activity exports."""

from nv_config_manager_workflows.activities.ticketing.activities import (
    add_ticket_comment,
    upload_attachment,
    validate_ticket,
)
from nv_config_manager_workflows.activities.ticketing.models import (
    AddCommentInput,
    AddCommentOutput,
    UploadAttachmentInput,
    UploadAttachmentOutput,
    ValidateTicketInput,
    ValidateTicketOutput,
)
from nv_config_manager_workflows.clients.ticketing.jira import JiraClientError

TICKETING_ACTIVITIES = (
    validate_ticket,
    upload_attachment,
    add_ticket_comment,
)

__all__ = [
    "AddCommentInput",
    "AddCommentOutput",
    "JiraClientError",
    "TICKETING_ACTIVITIES",
    "UploadAttachmentInput",
    "UploadAttachmentOutput",
    "ValidateTicketInput",
    "ValidateTicketOutput",
    "add_ticket_comment",
    "upload_attachment",
    "validate_ticket",
]
