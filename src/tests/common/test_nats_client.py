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
"""Tests for JetStream API prefix handling in the base NATS clients."""

import asyncio
from configparser import ConfigParser
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from nats.js.api import (
    AckPolicy,
    DeliverPolicy,
    DiscardPolicy,
    RetentionPolicy,
    SequenceInfo,
    StorageType,
    StreamConfig,
)
from nats.js.errors import BadRequestError, NotFoundError
from nv_config_manager_infrastructure.nats.admin import (
    ARCHIVE_CONSUMER_MAX_ACK_PENDING,
    ARCHIVE_CONSUMER_MAX_DELIVER,
)

from nv_config_manager.common.client.nats import (
    DEFAULT_NATS_API_PREFIX,
    NatsClient,
    NatsConsumer,
)
from nv_config_manager.common.config.nats import (
    LOCAL_STREAM_MAX_BYTES,
    LocalStreamConfigMismatch,
    ensure_local_streams,
    nats_connection,
)

TEST_SERVER = "nats://nats.example.local:4222"


def _config(**overrides: str) -> ConfigParser:
    config = ConfigParser()
    config["nats"] = {
        "server": TEST_SERVER,
        "queue": "nv-config-manager",
        "config_manager_stream": "nv-config-manager",
        **overrides,
    }
    return config


def test_client_defaults_to_standard_api_prefix():
    """A client with no configured prefix uses the JetStream default."""
    client = NatsClient(server=TEST_SERVER)
    assert client.api_prefix == DEFAULT_NATS_API_PREFIX == "$JS.API"


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_method", ["password", "JWT"])
@pytest.mark.parametrize("scheme,local", [("tls", False), ("wss", False), ("nats", True)])
async def test_render_connection_tls_policy(auth_method: str, scheme: str, local: bool) -> None:
    """Render uses TLS-first only for native TLS endpoints."""
    server = f"{scheme}://nats.example.test:4222"
    config = _config(
        server=server,
        local=str(local),
        auth_method=auth_method,
        user="test-user",
        password="test-password",
        credentials="/test/user.creds",
    )
    conn = MagicMock()
    conn.jetstream.return_value.stream_info = AsyncMock()
    reconnected = AsyncMock()
    with (
        patch("nv_config_manager.common.config.loader.load_config", return_value=config),
        patch(
            "nv_config_manager.common.config.nats.connect", new=AsyncMock(return_value=conn)
        ) as connect,
    ):
        assert await nats_connection(reconnected_cb=reconnected) is conn

    assert connect.await_args.args == (server,)
    options = connect.await_args.kwargs
    if scheme == "tls":
        assert options["tls_handshake_first"] is True
    else:
        assert "tls_handshake_first" not in options
    assert options["tls"].check_hostname
    assert options["reconnected_cb"] is reconnected
    assert options["allow_reconnect"] is True
    if auth_method == "JWT":
        assert options["user_credentials"] == "/test/user.creds"
    else:
        assert options["user"] == "test-user"
        assert options["password"] == "test-password"
    assert conn.jetstream.return_value.stream_info.await_count == (2 if local else 0)


def _local_conn(missing: set[str]) -> MagicMock:
    """Return a connection whose JetStream reports the given streams as missing."""
    conn = MagicMock()
    jetstream = conn.jetstream.return_value

    async def stream_info(name: str) -> MagicMock:
        if name in missing:
            raise NotFoundError(code=404, err_code=10059, description="stream not found")
        return MagicMock()

    jetstream.stream_info = AsyncMock(side_effect=stream_info)
    jetstream.add_stream = AsyncMock()
    return conn


def _local_config(**overrides: str) -> ConfigParser:
    settings = {
        "local": "true",
        "config_manager_stream": "kiwi",
        "config_manager_subjects": "kiwi.>",
        "nautobot_stream": "nautobot",
        "nautobot_subjects": "nautobot",
    }
    return _config(**{**settings, **overrides})


@pytest.mark.asyncio
async def test_ensure_local_streams_creates_missing_streams_like_nats_ready():
    """Missing bundled streams use the configured names and nats-ready's limits."""
    conn = _local_conn(missing={"kiwi", "nautobot"})

    created = await ensure_local_streams(conn, _local_config())

    assert created == ["kiwi", "nautobot"]
    configs = {
        call.args[0].name: call.args[0]
        for call in conn.jetstream.return_value.add_stream.await_args_list
    }
    assert configs["kiwi"].subjects == ["kiwi.>"]
    assert configs["nautobot"].subjects == ["nautobot"]
    for config in configs.values():
        assert config.max_bytes == LOCAL_STREAM_MAX_BYTES
        assert config.storage == StorageType.FILE
        assert config.retention == RetentionPolicy.LIMITS
        assert config.discard == DiscardPolicy.OLD
        assert config.duplicate_window == 120.0


@pytest.mark.asyncio
async def test_ensure_local_streams_skips_existing_streams():
    """Streams that already exist are left untouched."""
    conn = _local_conn(missing=set())

    assert await ensure_local_streams(conn, _local_config()) == []
    conn.jetstream.return_value.add_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_ensure_local_streams_is_noop_for_external_nats():
    """Externally managed NATS owns its streams, so nothing is looked up or created."""
    conn = _local_conn(missing={"kiwi", "nautobot"})

    assert await ensure_local_streams(conn, _local_config(local="false")) == []
    conn.jetstream.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_local_streams_can_target_one_stream():
    """A consumer recovering its own stream does not touch the others."""
    conn = _local_conn(missing={"kiwi", "nautobot"})

    created = await ensure_local_streams(conn, _local_config(), streams=["nautobot"])

    assert created == ["nautobot"]
    conn.jetstream.return_value.stream_info.assert_awaited_once_with("nautobot")


@pytest.mark.asyncio
async def test_ensure_local_streams_uses_configured_max_bytes():
    """The chart-rendered limit wins so both creators agree on small dev installs."""
    conn = _local_conn(missing={"nautobot"})

    await ensure_local_streams(conn, _local_config(local_stream_max_bytes="104857600"))

    config = conn.jetstream.return_value.add_stream.await_args.args[0]
    assert config.max_bytes == 104857600


@pytest.mark.asyncio
async def test_ensure_local_streams_includes_separate_dcim_stream():
    """A distinct DCIM event stream is created alongside the Nautobot stream."""
    conn = _local_conn(missing={"dcim"})

    created = await ensure_local_streams(
        conn, _local_config(dcim_change_stream="dcim", dcim_change_subject="dcim.events")
    )

    assert created == ["dcim"]
    config = conn.jetstream.return_value.add_stream.await_args.args[0]
    assert config.subjects == ["dcim.events"]


def _stored_stream(
    name: str, subjects: list[str], *, max_bytes: int = LOCAL_STREAM_MAX_BYTES
) -> MagicMock:
    info = MagicMock()
    info.config = StreamConfig(
        name=name,
        subjects=subjects,
        retention=RetentionPolicy.LIMITS,
        storage=StorageType.FILE,
        discard=DiscardPolicy.OLD,
        max_bytes=max_bytes,
        duplicate_window=120.0,
        num_replicas=1,
    )
    return info


def _conflict_after_missing(conn: MagicMock, stored: MagicMock) -> None:
    """Report the stream missing once, then return the stream another creator stored."""
    jetstream = conn.jetstream.return_value
    lookups = 0

    async def stream_info(name: str) -> MagicMock:
        nonlocal lookups
        if name != "nautobot":
            return MagicMock()
        lookups += 1
        if lookups == 1:
            raise NotFoundError(code=404, err_code=10059, description="stream not found")
        return stored

    jetstream.stream_info = AsyncMock(side_effect=stream_info)
    jetstream.add_stream.side_effect = BadRequestError(
        code=400, err_code=10058, description="stream name already in use"
    )


@pytest.mark.asyncio
async def test_ensure_local_streams_separate_dcim_stream_replaces_nautobot_stream():
    """Like nats-ready, a separate DCIM stream is created instead of the Nautobot stream.

    With only the stream overridden, the DCIM subject is still ``nautobot``; also
    creating a ``nautobot`` stream would overlap that subject and fail.
    """
    conn = _local_conn(missing={"kiwi", "nautobot", "dcim"})

    created = await ensure_local_streams(conn, _local_config(dcim_change_stream="dcim"))

    assert created == ["kiwi", "dcim"]
    config = conn.jetstream.return_value.add_stream.await_args.args[0]
    assert config.subjects == ["nautobot"]


@pytest.mark.asyncio
async def test_ensure_local_streams_overridden_dcim_subject_replaces_nautobot_subjects():
    """Like nats-ready, an overridden DCIM subject is the shared stream's only subject."""
    conn = _local_conn(missing={"nautobot"})

    await ensure_local_streams(
        conn,
        _local_config(
            nautobot_subjects="nautobot,nautobot.extra", dcim_change_subject="dcim.events"
        ),
    )

    config = conn.jetstream.return_value.add_stream.await_args.args[0]
    assert config.subjects == ["dcim.events"]


@pytest.mark.asyncio
async def test_ensure_local_streams_default_dcim_subject_keeps_nautobot_subjects():
    """Without a DCIM subject override, the stream keeps every configured Nautobot subject."""
    conn = _local_conn(missing={"nautobot"})

    await ensure_local_streams(conn, _local_config(nautobot_subjects="nautobot,nautobot.extra"))

    config = conn.jetstream.return_value.add_stream.await_args.args[0]
    assert config.subjects == ["nautobot", "nautobot.extra"]


@pytest.mark.asyncio
async def test_ensure_local_streams_renamed_streams_keep_the_subjects_services_use():
    """Renaming a stream without listing subjects still stores the events services publish."""
    conn = _local_conn(missing={"kiwi", "changelog"})
    config = _config(local="true", config_manager_stream="kiwi", nautobot_stream="changelog")

    await ensure_local_streams(conn, config)

    configs = {
        call.args[0].name: call.args[0]
        for call in conn.jetstream.return_value.add_stream.await_args_list
    }
    assert configs["kiwi"].subjects == [
        "nv-config-manager.nautobotchange",
        "nv-config-manager.devicechange",
        "nv-config-manager.workflow.result",
    ]
    assert configs["changelog"].subjects == ["nautobot"]


@pytest.mark.asyncio
async def test_ensure_local_streams_accepts_concurrent_creation_when_config_matches():
    """Another replica creating the same stream first is not an error."""
    conn = _local_conn(missing=set())
    _conflict_after_missing(conn, _stored_stream("nautobot", ["nautobot"]))

    assert await ensure_local_streams(conn, _local_config()) == []


@pytest.mark.asyncio
async def test_ensure_local_streams_rejects_name_conflict_with_different_config():
    """Error 10058 is not success when the stored stream would drop configured events."""
    conn = _local_conn(missing=set())
    _conflict_after_missing(conn, _stored_stream("nautobot", ["other"], max_bytes=1))

    with pytest.raises(LocalStreamConfigMismatch, match="subjects"):
        await ensure_local_streams(conn, _local_config())


@pytest.mark.asyncio
async def test_ensure_local_streams_raises_other_creation_errors():
    """Unexpected creation failures surface to the caller."""
    conn = _local_conn(missing={"nautobot"})
    conn.jetstream.return_value.add_stream.side_effect = BadRequestError(
        code=400, err_code=10047, description="insufficient storage resources available"
    )

    with pytest.raises(BadRequestError):
        await ensure_local_streams(conn, _local_config())


def test_from_config_reads_config_manager_api_prefix():
    """from_config picks up the config-manager account prefix."""
    client = NatsClient.from_config(_config(config_manager_api_prefix="$JS.CUSTOM.API"))
    assert client.api_prefix == "$JS.CUSTOM.API"


def test_from_config_without_prefix_falls_back_to_default():
    """An absent prefix key leaves the client on the default prefix."""
    assert NatsClient.from_config(_config()).api_prefix == DEFAULT_NATS_API_PREFIX


@pytest.mark.asyncio
async def test_external_connect_does_not_require_stream_info():
    """Externally managed streams can be used without stream administration permission."""
    client = NatsClient(server=TEST_SERVER, local=False)
    conn = MagicMock(connected_url=TEST_SERVER)

    with (
        patch(
            "nv_config_manager_infrastructure.nats.client.nats.connect",
            new=AsyncMock(return_value=conn),
        ),
        patch.object(client, "_ensure_stream", new_callable=AsyncMock) as ensure_stream,
    ):
        await client.connect()

    ensure_stream.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_connect_keeps_stream_setup():
    """Bundled/local clients retain automatic stream setup."""
    client = NatsClient(server=TEST_SERVER, local=True)
    conn = MagicMock(connected_url=TEST_SERVER)

    with (
        patch(
            "nv_config_manager_infrastructure.nats.client.nats.connect",
            new=AsyncMock(return_value=conn),
        ),
        patch.object(client, "_ensure_stream", new_callable=AsyncMock) as ensure_stream,
    ):
        await client.connect()

    ensure_stream.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_stream_uses_configured_api_prefix():
    """Stream lookups go through the account's rewritten API prefix."""
    client = NatsClient(server=TEST_SERVER, api_prefix="$JS.CUSTOM.API")
    client.conn = MagicMock()
    client.conn.jetstream.return_value.stream_info = AsyncMock()

    await client._ensure_stream()

    client.conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")


@pytest.mark.asyncio
async def test_consumer_binds_existing_durable_with_configured_api_prefix():
    """An existing push consumer is inspected and bound through its account prefix."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        api_prefix="$JS.CUSTOM.API",
        durable_name="nv-config-manager-archive",
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    conn.is_closed = True
    expected = consumer._expected_consumer_config()
    conn.jetstream.return_value.consumer_info = AsyncMock(return_value=MagicMock(config=expected))
    conn.jetstream.return_value.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    conn.jetstream.assert_called_once_with(prefix="$JS.CUSTOM.API")
    conn.jetstream.return_value.consumer_info.assert_awaited_once_with(
        "nautobot", "nv-config-manager-archive"
    )
    kwargs = conn.jetstream.return_value.subscribe_bind.await_args.kwargs
    assert kwargs["stream"] == "nautobot"
    assert kwargs["consumer"] == "nv-config-manager-archive"
    assert kwargs["cb"] == consumer._dispatch
    config = kwargs["config"]
    assert config.durable_name == config.deliver_group == "nv-config-manager-archive"
    assert config.deliver_subject == "nv-config-manager.archive.delivery"
    assert config.deliver_policy == DeliverPolicy.NEW
    assert config.ack_policy == AckPolicy.EXPLICIT
    assert config.ack_wait == 360
    assert config.max_deliver == ARCHIVE_CONSUMER_MAX_DELIVER
    assert config.max_ack_pending == ARCHIVE_CONSUMER_MAX_ACK_PENDING
    assert kwargs["manual_ack"] is True


@pytest.mark.parametrize(("durable_max_deliver", "expected"), [(5, 5), (-1, -1), (None, -1)])
@pytest.mark.asyncio
async def test_consumer_adopts_bound_durable_delivery_limit(durable_max_deliver, expected):
    """JetStream enforces the durable's limit, so the consumer reports that one."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    config = consumer._expected_consumer_config()
    config.max_deliver = durable_max_deliver
    jetstream.consumer_info = AsyncMock(return_value=MagicMock(config=config))
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    assert consumer.max_deliver == expected


@pytest.mark.asyncio
async def test_recreated_consumer_replaces_previous_delivery_limit():
    """A durable created at startup is given up on at its own limit, not a stale one."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    consumer.max_deliver = 5
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    jetstream.consumer_info = AsyncMock(side_effect=NotFoundError)
    jetstream.add_consumer = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    assert consumer.max_deliver == ARCHIVE_CONSUMER_MAX_DELIVER


@pytest.mark.asyncio
async def test_consumer_creates_missing_durable_then_binds_it():
    """The runtime creates only its exact durable when it is absent."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        durable_name="nv-config-manager-archive",
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    created_info = MagicMock(config=consumer._expected_consumer_config())
    jetstream.consumer_info = AsyncMock(side_effect=NotFoundError)
    jetstream.add_consumer = AsyncMock(return_value=created_info)
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    jetstream.add_consumer.assert_awaited_once()
    create_kwargs = jetstream.add_consumer.await_args.kwargs
    assert create_kwargs["stream"] == "nv-config-manager"
    assert create_kwargs["config"].filter_subject == "nv-config-manager.workflow.result"
    jetstream.subscribe_bind.assert_awaited_once_with(
        stream="nv-config-manager",
        consumer="nv-config-manager-archive",
        config=created_info.config,
        cb=consumer._dispatch,
        manual_ack=True,
    )


@pytest.mark.asyncio
async def test_consumer_creation_race_binds_confirmed_durable():
    """A second replica winning creation does not prevent this replica from binding."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock(is_closed=True)
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    existing_info = MagicMock(config=consumer._expected_consumer_config())
    jetstream.consumer_info = AsyncMock(side_effect=[NotFoundError, existing_info])
    jetstream.add_consumer = AsyncMock(side_effect=RuntimeError("consumer already exists"))
    jetstream.subscribe_bind = AsyncMock()

    with patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn):
        await consumer.main()

    assert jetstream.consumer_info.await_count == 2
    jetstream.subscribe_bind.assert_awaited_once()


def test_consumer_mismatch_does_not_enforce_delivery_policy():
    """Migration delivery policy is accepted while operational drift is reported."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    config = consumer._expected_consumer_config()
    config.deliver_policy = DeliverPolicy.ALL
    assert consumer._consumer_configuration_mismatches(MagicMock(config=config)) == []

    config.ack_policy = AckPolicy.NONE
    assert "ack_policy" in consumer._consumer_configuration_mismatches(MagicMock(config=config))[0]


def test_consumer_mismatch_reports_unbounded_ack_pending():
    """A durable left at the server's default in-flight limit is reported."""
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    config = consumer._expected_consumer_config()
    config.max_ack_pending = 1000

    mismatches = consumer._consumer_configuration_mismatches(MagicMock(config=config))

    assert mismatches == [f"max_ack_pending=1000 expected {ARCHIVE_CONSUMER_MAX_ACK_PENDING!r}"]


def _dispatching_consumer(handler, manual_ack: bool = False) -> NatsConsumer:
    return NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=handler,
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
        manual_ack=manual_ack,
    )


def _delivery() -> MagicMock:
    msg = MagicMock(subject="nv-config-manager.workflow.result")
    msg.ack = AsyncMock()
    return msg


@pytest.mark.asyncio
async def test_consumer_handles_in_flight_deliveries_concurrently():
    """A slow message does not hold the next one in the client while its ack wait runs."""
    release = asyncio.Event()
    started = []

    async def handler(msg):
        started.append(msg)
        await release.wait()

    consumer = _dispatching_consumer(handler)
    first, second = _delivery(), _delivery()

    await consumer._dispatch(first)
    await consumer._dispatch(second)
    await asyncio.sleep(0)

    assert started == [first, second]
    release.set()
    await asyncio.gather(*consumer._handler_tasks)
    first.ack.assert_awaited_once()
    second.ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_caps_concurrent_handlers_when_asked():
    """A durable provisioned with a higher limit cannot start more handlers than the cap."""
    release = asyncio.Event()
    started = []

    async def handler(msg):
        started.append(msg)
        await release.wait()

    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=handler,
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
        max_concurrent_handlers=1,
    )
    first, second = _delivery(), _delivery()

    await consumer._dispatch(first)
    waiting = asyncio.create_task(consumer._dispatch(second))
    await asyncio.sleep(0)

    assert started == [first]
    assert len(consumer._handler_tasks) == 1
    release.set()
    await waiting
    await asyncio.gather(*consumer._handler_tasks)
    assert started == [first, second]


@pytest.mark.asyncio
async def test_consumer_leaves_failed_delivery_unacked():
    """A handler that raises leaves the message for JetStream to redeliver."""
    consumer = _dispatching_consumer(AsyncMock(side_effect=RuntimeError("boom")))
    msg = _delivery()

    await consumer._dispatch(msg)
    await asyncio.gather(*consumer._handler_tasks)

    msg.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_does_not_ack_for_manual_ack_handlers():
    """A handler that settles its own messages is not acked again."""
    consumer = _dispatching_consumer(AsyncMock(), manual_ack=True)
    msg = _delivery()

    await consumer._dispatch(msg)
    await asyncio.gather(*consumer._handler_tasks)

    msg.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_consumer_drains_in_flight_handlers_before_closing():
    """On shutdown deliveries stop, and running handlers settle before the connection closes."""
    events = []

    async def handler(msg):
        await asyncio.sleep(0.01)
        events.append("handled")

    consumer = _dispatching_consumer(handler)
    conn = MagicMock(is_closed=False)
    conn.close = AsyncMock(side_effect=lambda: events.append("closed"))
    consumer._subscription = MagicMock()
    consumer._subscription.unsubscribe = AsyncMock(side_effect=lambda: events.append("stopped"))

    await consumer._dispatch(_delivery())
    await consumer._drain_handlers(conn)
    await consumer._close_connection(conn)

    assert events == ["stopped", "handled", "closed"]


@pytest.mark.asyncio
async def test_consumer_cancels_handlers_that_outlast_the_drain():
    """A handler still running after the drain window is cancelled and redelivered later."""

    async def hang(_msg):
        await asyncio.sleep(10)

    consumer = _dispatching_consumer(hang)
    msg = _delivery()

    await consumer._dispatch(msg)
    with patch("nv_config_manager_infrastructure.nats.consumer.HANDLER_DRAIN_SECONDS", 0.01):
        await consumer._drain_handlers(MagicMock(is_closed=True))

    assert not consumer._handler_tasks
    msg.ack.assert_not_awaited()


@pytest.mark.asyncio
async def test_clean_exit_stops_the_consumer_loop():
    """A termination signal ends the loop so main drains handlers and closes the connection."""
    consumer = _dispatching_consumer(AsyncMock())
    conn = MagicMock(is_closed=False)
    conn.close = AsyncMock()
    conn.jetstream.return_value.consumer_info = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    subscription = MagicMock()
    subscription.unsubscribe = AsyncMock()
    conn.jetstream.return_value.subscribe_bind = AsyncMock(return_value=subscription)

    async def stop_after_first_sleep(_seconds):
        consumer._clean_exit()

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.sleep",
            side_effect=stop_after_first_sleep,
        ),
    ):
        await consumer.main()

    subscription.unsubscribe.assert_awaited_once()
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_consumer_stays_alive_after_subscribing():
    """The consumer loop remains active until the NATS connection closes."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    type(conn).is_closed = PropertyMock(side_effect=[False, True, True])
    conn.jetstream.return_value.consumer_info = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    conn.jetstream.return_value.subscribe_bind = AsyncMock()

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.sleep",
            new_callable=AsyncMock,
        ) as sleep,
    ):
        await consumer.main()

    sleep.assert_awaited_once_with(1)
    conn.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_point", ["ensure", "subscribe"])
async def test_consumer_closes_connection_when_startup_fails(failure_point):
    """A connection established before a startup failure is always closed."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    conn = MagicMock()
    conn.close = AsyncMock()
    jetstream = conn.jetstream.return_value
    consumer_info = MagicMock(config=consumer._expected_consumer_config())
    startup_error = RuntimeError(f"{failure_point} failed")
    ensure_side_effect = startup_error if failure_point == "ensure" else None
    subscribe_side_effect = startup_error if failure_point == "subscribe" else None

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch.object(
            consumer,
            "_ensure_consumer",
            new_callable=AsyncMock,
            return_value=consumer_info,
            side_effect=ensure_side_effect,
        ),
    ):
        jetstream.subscribe_bind = AsyncMock(side_effect=subscribe_side_effect)
        with pytest.raises(RuntimeError, match=f"{failure_point} failed"):
            await consumer.main()

    conn.close.assert_awaited_once()


def test_consumer_run_closes_event_loop_when_main_fails():
    """The event loop is closed even when the consumer coroutine raises."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )
    loop = MagicMock()
    loop.run_until_complete.side_effect = RuntimeError("startup failed")

    with (
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.get_running_loop",
            side_effect=RuntimeError,
        ),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.new_event_loop",
            return_value=loop,
        ),
        patch.object(consumer, "main", new=MagicMock(return_value=MagicMock())),
        pytest.raises(RuntimeError, match="startup failed"),
    ):
        consumer.run()

    loop.close.assert_called_once_with()


@pytest.mark.asyncio
async def test_consumer_run_rejects_running_event_loop():
    """The synchronous runner does not take ownership of a caller's event loop."""
    consumer = NatsConsumer(
        stream="nautobot",
        subject="nautobot",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
    )

    with (
        patch.object(consumer, "main", new=MagicMock()) as main,
        pytest.raises(RuntimeError, match="cannot be called from a running event loop"),
    ):
        consumer.run()

    main.assert_not_called()
    assert consumer._loop is None


def _liveness_consumer(liveness_timeout: float = 600) -> NatsConsumer:
    consumer = NatsConsumer(
        stream="nv-config-manager",
        subject="nv-config-manager.workflow.result",
        queue_suffix="archive",
        handler=AsyncMock(),
        server=TEST_SERVER,
        deliver_subject="nv-config-manager.archive.delivery",
        liveness_timeout=liveness_timeout,
    )
    consumer.conn = MagicMock(is_closed=False, is_connected=True)
    consumer._subscribed = True
    return consumer


def _durable_info(
    delivered: int, acked: int, num_pending: int = 0, num_ack_pending: int = 0
) -> MagicMock:
    return MagicMock(
        delivered=SequenceInfo(consumer_seq=delivered, stream_seq=delivered),
        ack_floor=SequenceInfo(consumer_seq=acked, stream_seq=acked),
        num_pending=num_pending,
        num_ack_pending=num_ack_pending,
    )


@pytest.mark.parametrize("liveness_timeout", [float("nan"), float("inf"), 360, 120])
def test_consumer_rejects_liveness_timeout_that_cannot_restart_safely(liveness_timeout):
    """The budget must be finite and outlast the ack wait of one slow message."""
    with pytest.raises(ValueError, match="liveness timeout"):
        _liveness_consumer(liveness_timeout)


def test_consumer_liveness_stays_live_until_subscribed():
    """A slow connect or durable check does not fail the probe before startup finishes."""
    consumer = _liveness_consumer()
    consumer._subscribed = False
    consumer.conn = None
    consumer._last_progress -= 3600

    assert consumer.liveness() == (
        True,
        "durable nv-config-manager-archive is not subscribed yet",
    )


def test_consumer_liveness_fails_when_connection_is_closed():
    """A closed connection is never live."""
    consumer = _liveness_consumer()
    consumer.conn.is_closed = True

    assert consumer.liveness() == (False, "NATS connection is not open")


def test_consumer_liveness_fails_after_durable_stops_progressing():
    """Outstanding messages with unchanged sequences stall liveness."""
    consumer = _liveness_consumer()
    consumer._record_consumer_info(_durable_info(delivered=10, acked=8, num_pending=5))
    consumer._last_progress -= 601
    consumer._record_consumer_info(_durable_info(delivered=10, acked=8, num_pending=5))

    live, detail = consumer.liveness()

    assert not live
    assert "made no progress for 601s" in detail


@pytest.mark.parametrize(
    "info",
    [
        _durable_info(delivered=11, acked=8, num_pending=4),
        _durable_info(delivered=10, acked=9, num_pending=5),
        _durable_info(delivered=10, acked=10),
    ],
    ids=["delivered-advanced", "ack-floor-advanced", "caught-up"],
)
def test_consumer_liveness_refreshes_when_durable_progresses_or_is_idle(info):
    """Advancing sequences or an empty backlog keep the consumer live."""
    consumer = _liveness_consumer()
    consumer._record_consumer_info(_durable_info(delivered=10, acked=8, num_pending=5))
    consumer._last_progress -= 601

    consumer._record_consumer_info(info)

    assert consumer.liveness()[0]


def test_consumer_liveness_ignores_redeliveries():
    """A redelivered message advances only the consumer sequence, which is not progress."""
    consumer = _liveness_consumer()
    consumer._record_consumer_info(_durable_info(delivered=10, acked=9, num_ack_pending=1))
    consumer._last_progress -= 601
    redelivered = _durable_info(delivered=10, acked=9, num_ack_pending=1)
    redelivered.delivered = SequenceInfo(consumer_seq=12, stream_seq=10)

    consumer._record_consumer_info(redelivered)

    assert not consumer.liveness()[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("consumer_info", "connected", "refreshed"),
    [
        (AsyncMock(return_value=_durable_info(delivered=1, acked=1)), True, True),
        (AsyncMock(side_effect=TimeoutError("consumer info timed out")), True, False),
        (AsyncMock(return_value=_durable_info(delivered=1, acked=1)), False, False),
    ],
    ids=["info-succeeds", "info-fails", "reconnecting"],
)
async def test_consumer_monitor_refreshes_only_from_connected_consumer_info(
    consumer_info, connected, refreshed
):
    """Failed or stale consumer info lets liveness run out."""
    consumer = _liveness_consumer()
    consumer._last_progress -= 601
    type(consumer.conn).is_closed = PropertyMock(side_effect=[False, True])
    consumer.conn.is_connected = connected
    jetstream = MagicMock(consumer_info=consumer_info)

    with patch(
        "nv_config_manager_infrastructure.nats.consumer.asyncio.sleep", new_callable=AsyncMock
    ) as sleep:
        await consumer._monitor_liveness(jetstream)

    consumer_info.assert_awaited_once_with("nv-config-manager", "nv-config-manager-archive")
    sleep.assert_awaited_once_with(15.0)
    type(consumer.conn).is_closed = PropertyMock(return_value=False)
    assert consumer.liveness()[0] is refreshed


@pytest.mark.asyncio
async def test_consumer_main_monitors_durable_when_liveness_is_enabled():
    """main() starts polling the durable alongside the subscription."""
    consumer = _liveness_consumer()
    conn = MagicMock(is_connected=True)
    conn.close = AsyncMock()
    checks = iter([False, False, True])
    type(conn).is_closed = PropertyMock(side_effect=lambda: next(checks, True))
    conn.jetstream.return_value.consumer_info = AsyncMock(
        return_value=MagicMock(config=consumer._expected_consumer_config())
    )
    conn.jetstream.return_value.subscribe_bind = AsyncMock()
    monitor = AsyncMock()
    consumer._subscribed = False

    with (
        patch.object(consumer, "connect", new_callable=AsyncMock, return_value=conn),
        patch.object(consumer, "_monitor_liveness", monitor),
        patch(
            "nv_config_manager_infrastructure.nats.consumer.asyncio.sleep",
            new_callable=AsyncMock,
        ),
    ):
        await consumer.main()

    monitor.assert_called_once_with(conn.jetstream.return_value)
    assert consumer._subscribed
    conn.close.assert_awaited_once()
