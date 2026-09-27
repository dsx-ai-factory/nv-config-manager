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
"""Compatibility facade for package-owned ticketing activities."""

from nv_config_manager_workflows.activities.ticketing import (
    TICKETING_ACTIVITIES,
    AddCommentInput,
    AddCommentOutput,
    JiraClientError,
    UploadAttachmentInput,
    UploadAttachmentOutput,
    UploadTechSupportFromRedisInput,
    ValidateTicketInput,
    ValidateTicketOutput,
    add_ticket_comment,
    upload_attachment,
    upload_tech_support_from_redis,
    validate_ticket,
)

__all__ = [
    "AddCommentInput",
    "AddCommentOutput",
    "JiraClientError",
    "TICKETING_ACTIVITIES",
    "UploadAttachmentInput",
    "UploadAttachmentOutput",
    "UploadTechSupportFromRedisInput",
    "ValidateTicketInput",
    "ValidateTicketOutput",
    "add_ticket_comment",
    "upload_attachment",
    "upload_tech_support_from_redis",
    "validate_ticket",
]
