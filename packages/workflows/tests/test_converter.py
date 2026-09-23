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
"""Tests for the reusable Temporal payload compression codec."""

import base64
from pathlib import Path

from temporalio.api.common.v1 import Payload

from nv_config_manager_workflows.converter import (
    COMPRESSION_ENCODING,
    CompressionPayloadCodec,
    get_data_converter,
)

_FIXTURES = Path(__file__).with_name("fixtures")


async def test_compression_codec_encodes_with_gzip_metadata() -> None:
    """Encode sets the frozen binary/gzip encoding metadata."""
    encoded = await CompressionPayloadCodec().encode([Payload(data=b'{"key": "value"}')])

    assert len(encoded) == 1
    assert encoded[0].metadata.get("encoding", b"").decode() == COMPRESSION_ENCODING
    assert encoded[0].data


async def test_compression_codec_reduces_compressible_payloads() -> None:
    """Encode reduces the serialized size of repetitive payloads."""
    raw = b'{"config": "' + (b"line with repeated text " * 100) + b'"}'
    payload = Payload(data=raw)

    encoded = await CompressionPayloadCodec().encode([payload])

    assert len(encoded[0].data) < len(payload.SerializeToString())


async def test_compression_codec_round_trip() -> None:
    """Encode followed by decode returns the complete original payload."""
    payload = Payload(
        metadata={"encoding": b"json/plain", "messageType": b"example.Input"},
        data=b'{"workflow": "input", "large": "payload"}',
    )
    codec = CompressionPayloadCodec()

    decoded = await codec.decode(await codec.encode([payload]))

    assert decoded == [payload]


async def test_compression_codec_passes_through_unknown_encoding() -> None:
    """Decode leaves payloads without the gzip encoding unchanged."""
    payload = Payload(
        data=b'{"legacy": true}',
        metadata={"encoding": b"json/plain"},
    )

    decoded = await CompressionPayloadCodec().decode([payload])

    assert decoded == [payload]


async def test_pre_move_compressed_payload_decodes_for_gnicfd_6327() -> None:
    """GNICFD-6327 retains compatibility with payloads emitted by the old module path."""
    encoded = Payload.FromString(
        base64.b64decode((_FIXTURES / "compressed_payload.pb.b64").read_text())
    )

    decoded = await CompressionPayloadCodec().decode([encoded])

    assert decoded == [
        Payload(
            metadata={
                "encoding": b"json/plain",
                "messageType": b"nvcm.GNICFD-6327",
            },
            data=b'{"workflow":"pre-move-codec","version":1}',
        )
    ]


def test_get_data_converter_uses_compression_codec() -> None:
    """The converter factory installs a fresh compression codec."""
    converter = get_data_converter()

    assert isinstance(converter.payload_codec, CompressionPayloadCodec)
