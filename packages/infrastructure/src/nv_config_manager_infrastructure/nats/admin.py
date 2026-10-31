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
"""Operator-facing guidance for restricted NATS JetStream accounts."""

import nats.errors
from nats.js.errors import APIError

CONSUMER_ACK_WAIT_SECONDS = 360
CONSUMER_MAX_DELIVER = -1

# The archive handles one message at a time and a failing NVDataflow post can
# take ~150s, so every in-flight message must finish inside the ack wait:
# ARCHIVE_CONSUMER_MAX_ACK_PENDING * 150s < CONSUMER_ACK_WAIT_SECONDS.
ARCHIVE_CONSUMER_MAX_ACK_PENDING = 2
# A record that gives up is never archived, so with the archive's 5-minute
# retry delay this rides out about a day of backend outage.
ARCHIVE_CONSUMER_MAX_DELIVER = 288


def is_nats_permissions_error(exc: Exception) -> bool:
    """Return whether an exception represents a NATS authorization failure."""
    if isinstance(exc, nats.errors.AuthorizationError):
        return True
    if isinstance(exc, APIError) and exc.code in {401, 403}:
        return True
    message = str(exc).lower()
    return "permissions violation" in message or "authorization violation" in message


def consumer_api_subjects(api_prefix: str, stream: str, durable: str) -> str:
    """Return the exact consumer management subjects used by the render runtime."""
    return ", ".join(
        (
            f"{api_prefix}.CONSUMER.INFO.{stream}.{durable}",
            f"{api_prefix}.CONSUMER.DURABLE.CREATE.{stream}.{durable}",
            f"{api_prefix}.CONSUMER.DELETE.{stream}.{durable}",
            f"{api_prefix}.CONSUMER.MSG.NEXT.{stream}.{durable}",
        )
    )


def expected_consumer_configuration(durable: str, subject: str) -> str:
    """Return the fixed durable configuration an administrator should provision."""
    return (
        f"durable_name={durable!r}, filter_subject={subject!r}, "
        "deliver_policy='new', ack_policy='explicit', "
        f"ack_wait={CONSUMER_ACK_WAIT_SECONDS}s, max_deliver={CONSUMER_MAX_DELIVER}"
    )


def provision_consumer_request(api_prefix: str, stream: str, durable: str, subject: str) -> str:
    """Describe the request to make when the application cannot create a consumer."""
    return (
        "Ask the NATS administrator either to grant publish access to "
        f"{api_prefix}.CONSUMER.DURABLE.CREATE.{stream}.{durable} or to provision "
        f"the consumer on stream {stream!r} with "
        f"{expected_consumer_configuration(durable, subject)}."
    )


def update_consumer_request(stream: str, durable: str, subject: str) -> str:
    """Describe the request to align an externally managed consumer."""
    return (
        f"Ask the NATS administrator to update consumer {durable!r} on stream {stream!r} "
        f"to durable_name={durable!r}, filter_subject={subject!r}, ack_policy='explicit', "
        f"ack_wait={CONSUMER_ACK_WAIT_SECONDS}s, max_deliver={CONSUMER_MAX_DELIVER}. "
        "Delivery policy may retain the administrator's "
        "migration boundary choice."
    )


def reset_consumer_request(api_prefix: str, stream: str, durable: str, subject: str) -> str:
    """Describe the administrator action equivalent to an application reset."""
    return (
        "Ask the NATS administrator either to grant publish access to "
        f"{api_prefix}.CONSUMER.DELETE.{stream}.{durable} and "
        f"{api_prefix}.CONSUMER.DURABLE.CREATE.{stream}.{durable}, or to delete consumer "
        f"{durable!r} from stream {stream!r} and recreate it at the stream head with "
        f"{expected_consumer_configuration(durable, subject)}."
    )
