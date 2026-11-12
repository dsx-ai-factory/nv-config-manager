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
"""Liveness endpoint for long-running consumers."""

import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol


class LivenessCheck(Protocol):
    """A component that can report whether it is still making progress."""

    def liveness(self) -> tuple[bool, str]:
        """Return whether the component is live, and a short reason."""
        ...


def start_liveness_server(check: LivenessCheck, port: int) -> ThreadingHTTPServer:
    """Serve ``check.liveness()`` on /healthz from a daemon thread.

    A consumer that stops making progress often raises nothing, so its pod stays
    Running. Answering 503 lets a Kubernetes liveness probe restart it.
    """

    class LivenessHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") not in ("/healthz", ""):
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            live, detail = check.liveness()
            body = detail.encode()
            self.send_response(HTTPStatus.OK if live else HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            """Drop per-request logging so probes do not flood the log."""

    server = ThreadingHTTPServer(("", port), LivenessHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
