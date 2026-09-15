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
"""Store provider-owned device identifiers as text.

Revision ID: 004
Revises: 003
Create Date: 2026-08-10 00:00:00.000000
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """Allow non-UUID identifiers such as NetBox integer IDs."""
    op.alter_column(
        "config_files",
        "device_uuid",
        existing_type=postgresql.UUID(as_uuid=True),
        type_=sa.Text(),
        existing_nullable=False,
        postgresql_using="device_uuid::text",
    )


def downgrade() -> None:
    """Restore UUID storage when every persisted identifier is UUID-shaped."""
    op.alter_column(
        "config_files",
        "device_uuid",
        existing_type=sa.Text(),
        type_=postgresql.UUID(as_uuid=True),
        existing_nullable=False,
        postgresql_using="device_uuid::uuid",
    )
