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
"""Tests for the native ZTP HTTPS listener and certificate reload watcher."""

from unittest.mock import MagicMock, call, patch

from nv_config_manager.ztp.api import main as ztp_main


def test_main_configures_native_tls_and_starts_watcher() -> None:
    watcher = MagicMock()
    with (
        patch(
            "sys.argv",
            [
                "nv-config-manager-ztp-api",
                "--port",
                "8443",
                "--ssl-certfile",
                "/tls/tls.crt",
                "--ssl-keyfile",
                "/tls/tls.key",
            ],
        ),
        patch.object(ztp_main.threading, "Thread", return_value=watcher) as thread,
        patch.object(ztp_main.uvicorn, "run") as run,
    ):
        ztp_main.main()

    thread.assert_called_once_with(
        target=ztp_main._watch_tls_material,
        args=("/tls/tls.crt", "/tls/tls.key", 30.0),
        name="ztp-tls-certificate-watcher",
        daemon=True,
    )
    watcher.start.assert_called_once_with()
    assert run.call_args.kwargs["port"] == 8443
    assert run.call_args.kwargs["ssl_certfile"] == "/tls/tls.crt"
    assert run.call_args.kwargs["ssl_keyfile"] == "/tls/tls.key"
    assert run.call_args.kwargs["forwarded_allow_ips"] == "127.0.0.1"


def test_tls_watcher_terminates_process_after_secret_rotation() -> None:
    before = (1, 2, 3, 4)
    after = (5, 6, 7, 8)
    with (
        patch.object(ztp_main, "_tls_material_state", side_effect=[before, after]),
        patch.object(ztp_main.time, "sleep") as sleep,
        patch.object(ztp_main.os, "getpid", return_value=123),
        patch.object(ztp_main.os, "kill") as kill,
    ):
        ztp_main._watch_tls_material("/tls/tls.crt", "/tls/tls.key", 10)

    assert sleep.call_args_list == [call(10)]
    kill.assert_called_once_with(123, ztp_main.signal.SIGTERM)
