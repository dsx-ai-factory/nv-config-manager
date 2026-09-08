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
import argparse
import asyncio
import errno
import inspect
import io
import logging
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from typing import Any, cast
from uuid import uuid4

import paramiko
from paramiko import (
    ServerInterface,
    SFTPAttributes,
    SFTPHandle,
    SFTPServer,
    SFTPServerInterface,
)
from paramiko.channel import Channel
from paramiko.common import (
    AUTH_SUCCESSFUL,
    OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED,
    OPEN_SUCCEEDED,
)
from paramiko.sftp import (
    SFTP_FAILURE,
    SFTP_NO_SUCH_FILE,
    SFTP_OK,
    SFTP_PERMISSION_DENIED,
)
from paramiko.transport import Transport
from prometheus_client import start_http_server

from nv_config_manager.common.config import get_storage_client
from nv_config_manager.common.log import (
    EscapingLoggerAdapter,
    LogCategory,
    configure_logging,
    escape_log_newlines,
    get_logger,
)
from nv_config_manager.dcim import dcim_client_session
from nv_config_manager.ztp.device import DeviceData
from nv_config_manager.ztp.download_control import ThreadDownloadLimiter, get_positive_int_config
from nv_config_manager.ztp.storage import (
    ObjectStorageClient,
    ObjectStorageDownload,
    ObjectStorageException,
    ObjectStorageNotFoundException,
    record_storage_download,
)

logger = get_logger(__name__, category=LogCategory.ZTP)

# Global flag to control server shutdown
shutdown_event = threading.Event()

# SFTP clients normally issue small, sequential reads. Keeping one bounded range
# in memory avoids a full-object buffer while limiting S3 requests for large files.
SFTP_OBJECT_STORAGE_READ_AHEAD_BYTES = get_positive_int_config(
    "sftp_read_ahead_bytes", 16 * 1024 * 1024
)
SFTP_DOWNLOAD_LIMITER = ThreadDownloadLimiter(
    get_positive_int_config("sftp_max_concurrent_downloads", 8),
    protocol="sftp",
)


def is_localhost(addr: tuple[str, int]) -> bool:
    """Check if the connection is from localhost."""
    return addr[0] in ("127.0.0.1", "::1", "localhost")


def get_logger_for_addr(addr: tuple[str, int]) -> logging.Logger | logging.LoggerAdapter:
    """Get a logger with appropriate level based on client address."""
    if is_localhost(addr):
        healthcheck_logger = logging.getLogger(f"{__name__}.localhost")
        healthcheck_logger.setLevel(logging.WARNING)
        return EscapingLoggerAdapter(healthcheck_logger, extra={"category": LogCategory.ZTP})
    return logger


def signal_handler(signum: int, frame: Any) -> None:
    """Handle shutdown signals from Kubernetes."""
    logger.info("Received signal %d, initiating graceful shutdown", signum)
    shutdown_event.set()


class ZTPServer(ServerInterface):
    def check_auth_password(self, username: str, password: str) -> int:
        """Allow all password authentication attempts."""
        # all are allowed
        return cast(int, AUTH_SUCCESSFUL)

    def get_allowed_auths(self, username: str) -> str:
        """Return the list of allowed authentication methods."""
        return "password"

    def check_channel_request(self, kind: str, chanid: int) -> int:
        """Check if the requested channel type is allowed."""
        if kind == "session":
            return cast(int, OPEN_SUCCEEDED)
        return cast(int, OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED)


class ObjectStorageRangeReader:
    """Serve offset reads from object storage using one bounded range cache."""

    def __init__(
        self,
        *,
        storage_client: ObjectStorageClient,
        event_loop: asyncio.AbstractEventLoop,
        platform: str,
        version: str,
        filename: str,
        content_length: int,
        logger: logging.Logger | logging.LoggerAdapter,
        etag: str | None = None,
        read_ahead_bytes: int = SFTP_OBJECT_STORAGE_READ_AHEAD_BYTES,
        release_download_permit: Callable[[], None] | None = None,
    ) -> None:
        if content_length < 0:
            raise ValueError("content_length must not be negative")
        if read_ahead_bytes <= 0:
            raise ValueError("read_ahead_bytes must be positive")

        self._storage_client = storage_client
        self._event_loop = event_loop
        self._platform = platform
        self._version = version
        self._filename = filename
        self._content_length = content_length
        self._etag = etag
        self._logger = logger
        self._read_ahead_bytes = read_ahead_bytes
        self._cache_start = 0
        self._cache = b""
        self._closed = False
        self._failed = False
        self._started_at = time.monotonic()
        self._transfer_id = uuid4().hex
        self._bytes_fetched = 0
        self._bytes_served = 0
        self._download: ObjectStorageDownload | None = None
        self._release_download_permit = release_download_permit

    def read(self, offset: int, length: int) -> bytes:
        """Return the requested bytes, fetching a bounded S3 range when needed."""
        if self._closed:
            raise OSError(errno.EBADF, "Object storage reader is closed")
        if length <= 0 or offset >= self._content_length:
            return b""
        if offset < 0:
            raise OSError(errno.EINVAL, "SFTP offset must not be negative")

        requested_length = min(length, self._read_ahead_bytes)
        requested_end = min(offset + requested_length, self._content_length)
        cache_end = self._cache_start + len(self._cache)
        if not (self._cache_start <= offset and requested_end <= cache_end):
            self._load_cache(offset, requested_end)

        start = offset - self._cache_start
        result = self._cache[start : start + (requested_end - offset)]
        self._bytes_served += len(result)
        return result

    def close(self) -> None:
        """Close the storage session and record the final transfer outcome."""
        if self._closed:
            return
        self._closed = True
        duration_seconds = time.monotonic() - self._started_at

        if self._bytes_fetched and not self._failed:
            outcome = "completed" if self._bytes_served >= self._content_length else "partial"
            record_storage_download(
                backend=self._backend,
                protocol="sftp",
                outcome=outcome,
                bytes_received=self._bytes_fetched,
                duration_seconds=duration_seconds,
            )
            self._logger.info(
                "SFTP storage download completed",
                extra={
                    **self._log_fields(),
                    "outcome": outcome,
                    "content_length": self._content_length,
                    "bytes_fetched": self._bytes_fetched,
                    "bytes_served": self._bytes_served,
                    "duration_seconds": duration_seconds,
                    "bytes_per_second": (
                        self._bytes_served / duration_seconds if duration_seconds else 0
                    ),
                },
            )

        try:
            self._event_loop.run_until_complete(self._storage_client.close())
        except Exception:
            self._logger.exception("Failed to close SFTP object storage client")
        finally:
            if self._release_download_permit is not None:
                self._release_download_permit()
                self._release_download_permit = None

    @property
    def _backend(self) -> str:
        return self._download.backend if self._download is not None else "unknown"

    def _log_fields(self) -> dict[str, Any]:
        download = self._download
        return {
            "transfer_id": self._transfer_id,
            "storage_backend": self._backend,
            "storage_key": escape_log_newlines(
                download.object_key if download is not None else self._object_key
            ),
            "storage_endpoint": (
                escape_log_newlines(download.endpoint)
                if download is not None and download.endpoint
                else None
            ),
            "s3_request_id": (
                escape_log_newlines(download.request_id)
                if download is not None and download.request_id
                else None
            ),
        }

    @property
    def _object_key(self) -> str:
        return f"{self._platform}/{self._version}/{self._filename}"

    def _load_cache(self, offset: int, requested_end: int) -> None:
        cache_end = min(max(requested_end, offset + self._read_ahead_bytes), self._content_length)
        expected_length = cache_end - offset
        try:
            content = self._event_loop.run_until_complete(
                self._read_range(offset, cache_end - 1, expected_length)
            )
        except Exception as exc:
            self._record_failure(exc)
            raise
        self._cache_start = offset
        self._cache = content

    async def _read_range(self, start: int, end: int, expected_length: int) -> bytes:
        download = await self._storage_client.get_object(
            self._platform,
            self._version,
            self._filename,
            range_header=f"bytes={start}-{end}",
            known_total_length=self._content_length,
            if_match=self._etag,
        )
        try:
            chunks: list[bytes] = []
            bytes_read = 0
            while bytes_read < expected_length:
                read_result = download.file_handle.read(expected_length - bytes_read)
                chunk = cast(
                    bytes,
                    await read_result if inspect.isawaitable(read_result) else read_result,
                )
                if not chunk:
                    break
                chunks.append(chunk)
                bytes_read += len(chunk)
            content = b"".join(chunks)
            if len(content) != expected_length or download.content_length != expected_length:
                raise ObjectStorageException(
                    "Storage response did not satisfy requested range "
                    f"{start}-{end}: received {len(content)} bytes, expected {expected_length}"
                )
            self._download = download
            self._bytes_fetched += len(content)
            return content
        finally:
            close = getattr(download.file_handle, "close", None)
            if callable(close):
                close_result = close()
                if inspect.isawaitable(close_result):
                    await close_result

    def _record_failure(self, exc: Exception) -> None:
        if self._failed:
            return
        self._failed = True
        duration_seconds = time.monotonic() - self._started_at
        record_storage_download(
            backend=self._backend,
            protocol="sftp",
            outcome="failed",
            bytes_received=self._bytes_fetched,
            duration_seconds=duration_seconds,
        )
        self._logger.exception(
            "SFTP storage download failed",
            extra={
                **self._log_fields(),
                "content_length": self._content_length,
                "bytes_fetched": self._bytes_fetched,
                "bytes_served": self._bytes_served,
                "duration_seconds": duration_seconds,
                "error_type": type(exc).__name__,
                "error": escape_log_newlines(exc),
            },
        )


class ZTPSFTPHandle(SFTPHandle):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize a new SFTP file handle."""
        super().__init__(*args)
        self.readfile: io.StringIO | io.BytesIO | None = None
        self.range_reader: ObjectStorageRangeReader | None = None
        self.logger: logging.Logger | logging.LoggerAdapter = logger
        self.filename: str = ""

    def set_logger(self, logger: logging.Logger | logging.LoggerAdapter) -> None:
        """Set the logger for this handle."""
        self.logger = logger

    def close(self) -> int:  # type: ignore[override, ty:invalid-method-override]
        """Close the file handle and release resources."""
        self.logger.debug("Closing file handle for: %s", self.filename or "unknown")
        try:
            if self.readfile:
                self.readfile.close()
            if self.range_reader:
                self.range_reader.close()

        except Exception as e:
            self.logger.error("Error closing file handle: %s", e)
        return int(SFTP_OK)

    def read(self, offset: int, length: int) -> bytes | int:
        """Read a specified number of bytes from the file at the given offset."""
        self.logger.debug(
            "Reading %d bytes at offset %d from %s",
            length,
            offset,
            self.filename or "unknown",
        )
        try:
            if self.range_reader is not None:
                data = self.range_reader.read(offset, length)
                self.logger.debug("Read %d bytes successfully", len(data))
                return data
            if self.readfile is None:
                return SFTPServer.convert_errno(errno.ENOENT)
            self.readfile.seek(offset)
            file_data = self.readfile.read(length)
            if isinstance(file_data, str):
                file_data = file_data.encode("utf-8")
            self.logger.debug("Read %d bytes successfully", len(file_data))
            return file_data
        except OSError as e:
            self.logger.error("Error reading from file: %s", e)
            return SFTPServer.convert_errno(e.errno or errno.EIO)
        except Exception as exc:
            self.logger.exception("Error reading from object storage: %s", exc)
            return SFTP_FAILURE


class ZTPSFTPServer(SFTPServerInterface):
    def __init__(self, server: ServerInterface, *args: Any, **kwargs: Any) -> None:
        """Initialize the SFTP server with the given server instance and client address."""
        super().__init__(server)
        self._path_cache: dict[str, io.StringIO | io.BytesIO] = {}  # Cache for resolved paths
        self._client_addr: str | None = kwargs.get("client_addr")
        self.logger = get_logger_for_addr((self._client_addr or "unknown", 0))
        self.logger.info("SFTP server initialized for client %s", self._client_addr)

        # Create a dedicated event loop for this SFTP session
        # This avoids the overhead of creating/destroying loops for each async call
        self._event_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._event_loop)

    def _load_path(self, path: str) -> io.BytesIO:
        """Load file content from the given path."""
        if path in self._path_cache:
            self.logger.debug("Cache hit for path: %s", path)
            cached = self._path_cache[path]
            if isinstance(cached, io.BytesIO):
                return cached
            # Convert StringIO to BytesIO if needed
            return io.BytesIO(cached.getvalue().encode("utf-8"))

        self.logger.info("Request for path: %s from %s", path, self._client_addr)
        path_parts = path.split("/")
        if path_parts[1] == "device":
            content = self._load_ztp_file(path_parts[2], path_parts[3])
            self._path_cache[path] = content
            return content
        if path_parts[1] == "healthcheck":
            return io.BytesIO(b"OK")

        raise FileNotFoundError(f"Unknown path type: {path_parts[0]}")

    def _load_ztp_file(self, device_id: str, config_file: str) -> io.BytesIO:
        """Load a ZTP configuration file from the config store or return a cached version."""
        self.logger.info(
            "Loading file for Device: %s, Config: %s from %s",
            device_id,
            config_file,
            self._client_addr,
        )

        # Check if client is from localhost (bypass IP auth for testing/healthchecks)
        client_is_localhost = self._client_addr in ("127.0.0.1", "::1", "localhost")

        async def get_device_and_file() -> tuple[Any, str | None]:
            async with dcim_client_session() as client:
                device = DeviceData.from_dcim(await client.get_ztp_device(device_id))
            # Bypass IP validation for localhost connections (testing/port-forward)
            if not client_is_localhost and self._client_addr not in device.addresses:
                raise PermissionError(
                    f"Access denied. Expected IP(s): {device.addresses}, Actual IP: {self._client_addr}"
                )
            # Load file content
            file_content = await device.load_file(config_file)
            return device, file_content

        device, file_content = self._event_loop.run_until_complete(get_device_and_file())

        if not device or file_content is None:
            raise FileNotFoundError("File not found")

        # Convert string content to bytes
        return io.BytesIO(file_content.encode("utf-8"))

    def _open_s3_file(
        self, platform: str, version: str, filename: str, flags: int
    ) -> ZTPSFTPHandle:
        """Open a range-backed object-storage handle without buffering the object."""
        self.logger.info(
            "Opening range-backed object storage file: %s/%s/%s for %s",
            platform,
            version,
            filename,
            self._client_addr,
        )
        storage_client = get_storage_client()
        admission_wait_seconds = SFTP_DOWNLOAD_LIMITER.acquire()
        try:
            self._event_loop.run_until_complete(storage_client.connect())
            metadata = self._event_loop.run_until_complete(
                storage_client.get_object_metadata(platform, version, filename)
            )
            content_length = metadata.get("size")
            if not isinstance(content_length, int) or content_length < 0:
                raise ObjectStorageException(
                    f"Object metadata returned invalid size for {platform}/{version}/{filename}"
                )
            etag = metadata.get("etag")
            if etag is not None and not isinstance(etag, str):
                raise ObjectStorageException(
                    f"Object metadata returned invalid ETag for {platform}/{version}/{filename}"
                )
        except ObjectStorageNotFoundException as exc:
            try:
                self._event_loop.run_until_complete(storage_client.close())
            finally:
                SFTP_DOWNLOAD_LIMITER.release()
            raise FileNotFoundError(
                f"File not found in object storage: {platform}/{version}/{filename}"
            ) from exc
        except Exception:
            try:
                self._event_loop.run_until_complete(storage_client.close())
            finally:
                SFTP_DOWNLOAD_LIMITER.release()
            raise

        try:
            reader = ObjectStorageRangeReader(
                storage_client=storage_client,
                event_loop=self._event_loop,
                platform=platform,
                version=version,
                filename=filename,
                content_length=content_length,
                etag=etag,
                logger=self.logger,
                release_download_permit=SFTP_DOWNLOAD_LIMITER.release,
            )
            fobj = ZTPSFTPHandle(flags)
            fobj.set_logger(self.logger)
            fobj.filename = filename
            fobj.range_reader = reader
            self.logger.info(
                "SFTP storage download admitted",
                extra={
                    "storage_key": escape_log_newlines(f"{platform}/{version}/{filename}"),
                    "admission_wait_seconds": admission_wait_seconds,
                    "active_downloads": SFTP_DOWNLOAD_LIMITER.active,
                    "read_ahead_bytes": SFTP_OBJECT_STORAGE_READ_AHEAD_BYTES,
                },
            )
            self.logger.debug("Created range-backed SFTP handle for file: %s", fobj.filename)
            return fobj
        except Exception:
            try:
                self._event_loop.run_until_complete(storage_client.close())
            finally:
                SFTP_DOWNLOAD_LIMITER.release()
            raise

    def close_session(self) -> None:
        """Clear session resources after Paramiko has closed outstanding handles."""
        self.logger.debug("Clearing path cache and closing event loop")
        self._path_cache.clear()

        if self._event_loop and not self._event_loop.is_closed():
            self._event_loop.close()

    def _mock_stat(self, path: str) -> SFTPAttributes | int:
        """Return mock file attributes for the given path."""
        # Optimize for S3 files - use head_object instead of downloading the entire file
        path_parts = path.split("/")
        if path_parts[1] == "file" and len(path_parts) >= 5:
            # This is an S3 file path: /file/platform/version/filename
            platform, version, filename = (
                path_parts[2],
                path_parts[3],
                "/".join(path_parts[4:]),
            )
            return self._stat_s3_file(platform, version, filename)

        # For non-S3 paths, fall back to loading the full file
        try:
            file = self._load_path(path)
            self.logger.debug("Successfully loaded file for stat: %s", path)
        except Exception as exc:
            self.logger.error(
                "Error loading file %s for stat: %s",
                path,
                exc,
            )
            if isinstance(exc, FileNotFoundError):
                return SFTP_NO_SUCH_FILE
            if isinstance(exc, PermissionError):
                return SFTP_PERMISSION_DENIED
            return SFTP_FAILURE
        attr = SFTPAttributes()
        # Set the size of the file-like object
        if isinstance(file, io.StringIO):
            file.seek(0, os.SEEK_END)
            attr.st_size = file.tell()
            file.seek(0)  # Reset the file pointer
        elif isinstance(file, io.BytesIO):
            attr.st_size = len(file.getvalue())
        else:
            raise TypeError("Unsupported file type")

        # Set other attributes
        attr.st_uid = os.getuid()  # Current user ID
        attr.st_gid = os.getgid()  # Current group ID
        attr.st_mode = 0o100644  # Regular file with 644 permissions
        current_time = int(time.time())
        attr.st_atime = current_time  # Access time
        attr.st_mtime = current_time  # Modification time
        self.logger.debug(
            "Created attributes for file %s: size=%d",
            path,
            attr.st_size,
        )
        return attr

    def _stat_s3_file(self, platform: str, version: str, filename: str) -> SFTPAttributes | int:
        """Get file attributes for an S3 file using head_object (no download)."""
        self.logger.info(
            "Getting metadata for S3 file: %s/%s/%s",
            platform,
            version,
            filename,
        )
        storage_client = get_storage_client()
        try:

            async def get_metadata() -> dict[str, Any]:
                async with storage_client:
                    result = await storage_client.get_object_metadata(platform, version, filename)
                    return dict(result)

            metadata = self._event_loop.run_until_complete(get_metadata())

            attr = SFTPAttributes()
            attr.st_size = metadata["size"]
            attr.st_uid = os.getuid()
            attr.st_gid = os.getgid()
            attr.st_mode = 0o100644  # Regular file with 644 permissions

            # Use last_modified time if available
            if metadata.get("last_modified"):
                mtime = int(metadata["last_modified"].timestamp())
                attr.st_atime = mtime
                attr.st_mtime = mtime
            else:
                current_time = int(time.time())
                attr.st_atime = current_time
                attr.st_mtime = current_time

            self.logger.debug(
                "Created attributes for S3 file %s/%s/%s: size=%d",
                platform,
                version,
                filename,
                attr.st_size,
            )
            return attr
        except ObjectStorageNotFoundException:
            self.logger.error(
                "S3 file not found: %s/%s/%s",
                platform,
                version,
                filename,
            )
            return SFTP_NO_SUCH_FILE
        except Exception as exc:
            self.logger.error("Error getting S3 metadata: %s", exc)
            return SFTP_FAILURE

    def stat(self, path: str) -> SFTPAttributes | int:
        """Return file attributes for the given path."""
        self.logger.debug("stat request: %s", path)
        return self._mock_stat(path)

    def lstat(self, path: str) -> SFTPAttributes | int:
        """Return file attributes for the given path without following symbolic links."""
        self.logger.debug("lstat request: %s", path)
        return self._mock_stat(path)

    def open(self, path: str, flags: int, attr: SFTPAttributes | None) -> ZTPSFTPHandle | int:
        """Open a file handle for the given path with the specified flags."""
        self.logger.debug("open request: %s, flags: %s", path, flags)
        try:
            path_parts = path.split("/")
            if len(path_parts) >= 5 and path_parts[1] == "file":
                return self._open_s3_file(
                    path_parts[2],
                    path_parts[3],
                    path_parts[4],
                    flags,
                )
            file = self._load_path(path)
            self.logger.debug("Successfully loaded file for path: %s", path)
        except Exception as exc:
            self.logger.error(
                "Error loading file %s: %s",
                path,
                exc,
            )
            if isinstance(exc, FileNotFoundError):
                return SFTP_NO_SUCH_FILE
            if isinstance(exc, PermissionError):
                return SFTP_PERMISSION_DENIED
            return SFTP_FAILURE

        try:
            fobj = ZTPSFTPHandle(flags)
            fobj.set_logger(self.logger)  # Set the logger after creation
            fobj.filename = path.split("/")[-1]
            fobj.readfile = file
            self.logger.debug("Created SFTP handle for file: %s", fobj.filename)
            return fobj
        except Exception as exc:
            self.logger.error("Error creating SFTP handle: %s", exc)
            return SFTP_FAILURE


class ZTPSFTPSubsystemHandler(SFTPServer):
    """Close per-session resources after Paramiko closes all SFTP handles."""

    def finish_subsystem(self) -> None:
        """Preserve the event loop until outstanding range-backed handles are closed."""
        try:
            super().finish_subsystem()
        finally:
            server = cast(ZTPSFTPServer, self.server)
            server.close_session()


def handle_connection(
    conn: socket.socket, addr: tuple[str, int], host_key: paramiko.RSAKey
) -> None:
    """Handle an individual client connection to the SFTP server."""
    logger = get_logger_for_addr(addr)
    logger.info("New connection from %s", addr)
    transport: Transport | None = None
    try:
        transport = paramiko.Transport(conn)
        transport.add_server_key(host_key)

        # Load system moduli file for group exchange KEX
        moduli_path = "/etc/ssh/moduli"
        if os.path.exists(moduli_path):
            transport.load_server_moduli(filename=moduli_path)

        # Set up the SFTP subsystem handler
        transport.set_subsystem_handler(
            "sftp", ZTPSFTPSubsystemHandler, ZTPSFTPServer, client_addr=addr[0]
        )

        # Create server and start it
        server = ZTPServer()
        transport.start_server(server=server)

        # Wait for authentication and channel establishment
        channel: Channel | None = transport.accept(20)
        if channel is None:
            logger.error("No channel established")
            return

        # Keep the connection alive but with a timeout
        timeout: int = 3600
        start_time: float = time.time()

        while transport.is_active():
            # Check if we've been idle too long
            if time.time() - start_time > timeout:
                logger.info("Connection timeout after %d seconds", timeout)
                break
            time.sleep(1)
    except OSError as e:
        # Connection reset/closed by peer is normal for healthchecks from localhost
        if is_localhost(addr) and e.errno in (errno.ECONNRESET, errno.EPIPE, errno.ENOTCONN):
            logger.debug("Healthcheck connection closed: %s", e)
        else:
            logger.warning("Socket error from %s: %s", addr, e)
    except Exception as e:
        logger.exception("Error handling connection: %s", e)
    finally:
        try:
            if transport:
                transport.close()
        except Exception:  # nosec: B110
            pass
        conn.close()
        logger.info("Connection from %s closed", addr)


def start_server(host: str = "0.0.0.0", port: int = 8222, level: str = "INFO") -> None:  # nosec: B104
    """Start the SFTP server and listen for incoming connections."""
    # Set up signal handlers for graceful shutdown
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    os.environ.setdefault("LOG_LEVEL", level)
    configure_logging(service="ztp-sftp")

    # Set paramiko's log level to WARN to reduce noise
    logging.getLogger("paramiko").setLevel(logging.WARNING)

    metrics_port = get_positive_int_config("sftp_metrics_port", 9100)
    start_http_server(metrics_port, addr="0.0.0.0")  # nosec: B104
    logger.info("SFTP metrics server listening on 0.0.0.0:%d", metrics_port)

    # Generate host key once at startup (RSA key generation is CPU-intensive)
    logger.info("Generating RSA host key...")
    host_key = paramiko.RSAKey.generate(2048)
    logger.info("RSA host key generated")

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, True)
    server_socket.bind((host, port))
    server_socket.listen(10)

    logger.info("SFTP server listening on %s:%d", host, port)

    try:
        while not shutdown_event.is_set():
            # Set a timeout for accept to allow checking shutdown_event
            server_socket.settimeout(1)
            try:
                conn, addr = server_socket.accept()
                # Handle each client in a separate thread
                t = threading.Thread(target=handle_connection, args=(conn, addr, host_key))
                t.daemon = True
                t.start()
            except TimeoutError:
                # Timeout occurred, check if we should shutdown
                continue
            except Exception as e:
                if not shutdown_event.is_set():
                    logger.error("Error accepting connection: %s", e)
                    continue
                break

        logger.info("Server shutting down gracefully")

        # Wait for all active connections to complete
        active_threads = threading.enumerate()
        for thread in active_threads:
            if thread != threading.current_thread() and thread.is_alive():
                logger.info("Waiting for thread %s to complete", thread.name)
                thread.join(timeout=30)  # Give threads 30 seconds to complete

    except Exception as e:
        logger.error("Server error: %s", e)
    finally:
        server_socket.close()
        logger.info("Server shutdown complete")


def main() -> None:
    """Main entry point for the SFTP server."""
    parser = argparse.ArgumentParser(description="Start the SFTP server")
    parser.add_argument(
        "--host",
        default="0.0.0.0",  # nosec: B104
        help="Host to bind to (default: 0.0.0.0)",
    )
    parser.add_argument("--port", type=int, default=8222, help="Port to listen on (default: 8222)")
    parser.add_argument(
        "--level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: INFO)",
    )

    args = parser.parse_args()
    start_server(host=args.host, port=args.port, level=args.level)


if __name__ == "__main__":
    main()
