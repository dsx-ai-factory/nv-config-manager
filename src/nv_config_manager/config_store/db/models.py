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
"""SQLAlchemy database models."""

import enum
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, Index, Integer, LargeBinary, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class FileType(enum.StrEnum):
    """Enum for config file types."""

    INTENDED = "intended"
    BACKUP = "backup"


class Base(DeclarativeBase):
    """Base class for all database models."""

    pass


class ConfigFile(Base):
    """Config file version model.

    This table serves as the complete audit log for all configuration changes,
    with full version history, author information, and timestamps.
    """

    __tablename__ = "config_files"

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),  # type: ignore[call-overload]
        primary_key=True,
        default=uuid4,
    )
    device_uuid: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    filename: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[FileType] = mapped_column(
        Enum(FileType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=FileType.INTENDED,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)  # Compressed content
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)  # SHA256 of uncompressed
    author: Mapped[str] = mapped_column(Text, nullable=False)
    commit_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    __table_args__ = (
        UniqueConstraint(
            "device_uuid",
            "filename",
            "file_type",
            "version",
            name="uq_device_filename_filetype_version",
        ),
        Index("idx_config_files_device_filename", "device_uuid", "filename"),
        Index(
            "idx_config_files_device_filename_filetype",
            "device_uuid",
            "filename",
            "file_type",
        ),
        Index("idx_config_files_created_at", "created_at"),
        Index("idx_config_files_author", "author"),
    )

    def __repr__(self) -> str:
        return (
            f"<ConfigFile(device={self.device_uuid}, "
            f"filename={self.filename}, "
            f"type={self.file_type.value}, "
            f"version={self.version})>"
        )
