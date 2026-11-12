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
"""Liveness endpoint shared by the long-running consumers."""

from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from nv_config_manager_infrastructure.liveness import start_liveness_server


class _Check:
    def __init__(self) -> None:
        self.live = True

    def liveness(self) -> tuple[bool, str]:
        return self.live, "live" if self.live else "stalled"


def test_liveness_server_reports_the_check() -> None:
    """/healthz returns 200 while live, 503 once not, and 404 elsewhere."""
    check = _Check()
    server = start_liveness_server(check, port=0)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(f"{url}/healthz", timeout=5) as response:
            assert response.status == 200
            assert response.read() == b"live"

        check.live = False
        with pytest.raises(HTTPError) as stalled:
            urlopen(f"{url}/healthz", timeout=5)
        assert stalled.value.code == 503
        assert stalled.value.read() == b"stalled"

        with pytest.raises(HTTPError) as missing:
            urlopen(f"{url}/metrics", timeout=5)
        assert missing.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
