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
"""Temporal payload compression codec and data converter.

Compresses temporal payloads to stay under Temporal payload size limits.
"""

import dataclasses
import gzip
from collections.abc import Sequence

from temporalio.api.common.v1 import Payload
from temporalio.converter import DataConverter, PayloadCodec
from temporalio.converter import default as default_data_converter

COMPRESSION_ENCODING = "binary/gzip"


class CompressionPayloadCodec(PayloadCodec):
    """Payload codec that gzip-compresses payload data."""

    async def encode(self, payloads: Sequence[Payload]) -> list[Payload]:
        """Compress each payload's serialized form."""
        result: list[Payload] = []
        for p in payloads:
            result.append(
                Payload(
                    metadata={"encoding": COMPRESSION_ENCODING.encode()},
                    data=gzip.compress(p.SerializeToString()),
                )
            )
        return result

    async def decode(self, payloads: Sequence[Payload]) -> list[Payload]:
        """Decompress payloads that have our encoding; pass through others."""
        result: list[Payload] = []
        for p in payloads:
            encoding = p.metadata.get("encoding", b"").decode()
            if encoding != COMPRESSION_ENCODING:
                result.append(p)
                continue
            decompressed = gzip.decompress(p.data)
            result.append(Payload.FromString(decompressed))
        return result


def get_data_converter() -> DataConverter:
    """Return the default data converter with compression codec applied."""
    return dataclasses.replace(
        default_data_converter(),
        payload_codec=CompressionPayloadCodec(),
    )
