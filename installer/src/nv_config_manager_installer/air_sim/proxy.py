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
"""SOCKS proxy helpers for accessing NVCM services through DSX Air VMs."""

from __future__ import annotations

import os
import platform
import shlex
import subprocess
import time
from dataclasses import dataclass

from nv_config_manager_installer.air_sim.constants import NVCM_BOX_USER

SOCKS_PORT = 8080
_NVCM_URL = "https://nautobot.nvcm.air"

_SSH_OPTS = [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "UserKnownHostsFile=/dev/null",
    "-o",
    "PreferredAuthentications=password",
]

# Chromium-family executables to try, in preference order, per platform.
_CHROME_PATHS_DARWIN = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
)
_CHROME_PATHS_LINUX = (
    "google-chrome",
    "google-chrome-stable",
    "chromium-browser",
    "chromium",
    "brave-browser",
    "microsoft-edge",
)
_CHROME_PATHS_WINDOWS = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
)


@dataclass
class ProxyInfo:
    host: str
    port: int
    password: str
    socks_port: int = SOCKS_PORT
    target_url: str = _NVCM_URL

    # ── Per-platform command strings ──────────────────────────────────────────

    def ssh_cmd_unix(self) -> str:
        """SOCKS tunnel command for Linux / macOS (uses sshpass)."""
        return (
            f"sshpass -p {shlex.quote(self.password)}"
            f" ssh {' '.join(_SSH_OPTS)}"
            f" -D {self.socks_port} -N -p {self.port}"
            f" {NVCM_BOX_USER}@{self.host}"
        )

    def ssh_cmd_windows(self) -> str:
        """SOCKS tunnel command for Windows (built-in OpenSSH, prompts for password)."""
        return (
            f"ssh {' '.join(_SSH_OPTS)}"
            f" -D {self.socks_port} -N -p {self.port}"
            f" {NVCM_BOX_USER}@{self.host}"
        )

    def browser_cmd_unix(self) -> str:
        return (
            f'chromium-browser --proxy-server="socks5://localhost:{self.socks_port}"'
            f' --user-data-dir="/tmp/chrome-nvcm-proxy"'
            f" --ignore-certificate-errors {self.target_url}"
        )

    def browser_cmd_windows(self) -> str:
        """PowerShell-compatible Chrome launch command using the & call operator."""
        p = self.socks_port
        return (
            f"& 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe'"
            f" '--proxy-server=socks5://localhost:{p}'"
            f" '--user-data-dir=%TEMP%\\chrome-nvcm-proxy'"
            f" '--ignore-certificate-errors'"
            f" '{self.target_url}'"
        )

    def teleport_forward_cmd(self, remote_host: str) -> str:
        """Teleport local-forward command to expose the SOCKS port on Windows.

        Run this on the Windows machine to forward local:{socks_port} through
        the Teleport session to the Linux dev machine, where the SOCKS tunnel
        to the DSX Air VM is already running.  Then point your browser at
        socks5://localhost:{socks_port}.
        """
        return f"tsh ssh -L {self.socks_port}:localhost:{self.socks_port} {remote_host}"

    # ── Local launch ──────────────────────────────────────────────────────────

    def start_tunnel(self) -> subprocess.Popen[bytes] | None:
        """Start the SOCKS tunnel in the background; returns the Popen or None on failure."""
        system = platform.system()
        if system == "Windows":
            cmd = [
                "ssh",
                *_SSH_OPTS,
                "-D",
                str(self.socks_port),
                "-N",
                "-p",
                str(self.port),
                f"{NVCM_BOX_USER}@{self.host}",
            ]
        else:
            cmd = [
                "sshpass",
                "-p",
                self.password,
                "ssh",
                *_SSH_OPTS,
                "-D",
                str(self.socks_port),
                "-N",
                "-p",
                str(self.port),
                f"{NVCM_BOX_USER}@{self.host}",
            ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            time.sleep(1)
            if proc.poll() is not None:
                return None
            return proc
        except FileNotFoundError:
            return None

    def launch_browser(self) -> bool:
        """Launch a Chromium-family browser with the SOCKS proxy. Returns True on success."""
        system = platform.system()
        proxy_arg = f"--proxy-server=socks5://localhost:{self.socks_port}"
        user_data = r"%TEMP%\chrome-nvcm-proxy" if system == "Windows" else "/tmp/chrome-nvcm-proxy"
        extra = ["--ignore-certificate-errors", self.target_url]

        exe = _find_browser(system)
        if not exe:
            return False

        try:
            subprocess.Popen(
                [exe, proxy_arg, f"--user-data-dir={user_data}", *extra],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return True
        except Exception:
            return False


def _find_browser(system: str) -> str | None:
    if system == "Darwin":
        for p in _CHROME_PATHS_DARWIN:
            if os.path.isfile(p):
                return p
    elif system == "Windows":
        for p in _CHROME_PATHS_WINDOWS:
            if os.path.isfile(p):
                return p
    else:
        for name in _CHROME_PATHS_LINUX:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.returncode == 0:
                return result.stdout.strip()
    return None
