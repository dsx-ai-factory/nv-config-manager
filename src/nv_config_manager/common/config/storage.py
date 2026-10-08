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

from __future__ import annotations

import os

from nv_config_manager.common.config.loader import load_config
from nv_config_manager.ztp.filestore import FileStoreClient
from nv_config_manager.ztp.s3 import S3Client
from nv_config_manager.ztp.storage import ObjectStorageClient


def _nonblank_config_value(value: str | None) -> str | None:
    return value if value and value.strip() else None


def get_storage_client() -> ObjectStorageClient:
    """Return the appropriate storage client based on ZTP configuration.

    Uses [ztp] config values with environment variable fallback:
    - "file": Returns FileStoreClient
    - "s3" or unset: Returns S3Client (default)

    Returns:
        ObjectStorageClient implementation
    """
    config = load_config()
    ztp_config = config["ztp"] if config.has_section("ztp") else {}
    storage_type = ztp_config.get("storage_type") or os.environ.get("STORAGE_TYPE", "s3")
    storage_type = storage_type.lower()

    if storage_type == "file":
        file_store_path = ztp_config.get("file_store_path") or os.environ.get("FILE_STORE_PATH")
        if not file_store_path:
            raise ValueError("storage_type is 'file' but file_store_path is not set.")
        return FileStoreClient(base_path=file_store_path)
    return S3Client(
        bucket=_nonblank_config_value(ztp_config.get("s3_bucket")),
        custom_endpoint=_nonblank_config_value(ztp_config.get("s3_endpoint")),
        region=_nonblank_config_value(ztp_config.get("s3_region")),
        custom_access_key=_nonblank_config_value(ztp_config.get("s3_access_key")),
        custom_secret_key=_nonblank_config_value(ztp_config.get("s3_secret_key")),
    )
