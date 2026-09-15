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
"""Pytest configuration and fixtures."""

import os
import tempfile
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Create test INI file before any imports
_test_ini_content = """[config_store]
database_host = localhost
database_port = 5432
database = test_db
database_user = test_user
database_password = test_pass

[auth]
required = true
accept_request_headers = true

[redis]
host = localhost
port = 6379
db = 0
password =
ssl = false
socket_timeout = 5
socket_connect_timeout = 5

[nautobot]
server = https://nautobot.example.com
token = DUMMY
verify = true
cache_enabled = false
cache_refresh_interval = 3600
cache_ttl = 86400
"""

# Create temporary test INI file
_test_ini_fd, _test_ini_path = tempfile.mkstemp(suffix=".ini", text=True)
with os.fdopen(_test_ini_fd, "w") as f:
    f.write(_test_ini_content)

# Set environment variable to point to test INI
os.environ["NV_CONFIG_MANAGER_INI"] = _test_ini_path

# Now we can safely import the application
from nv_config_manager.config_store.api.main import app  # noqa: E402
from nv_config_manager.config_store.db import Base, get_db  # noqa: E402

# Use in-memory SQLite for testing (no external dependencies)
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


def pytest_sessionfinish(session, exitstatus):
    """Cleanup test INI file after test session."""
    try:
        os.unlink(_test_ini_path)
    except Exception:
        pass


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest.fixture(scope="function")
async def engine():
    """Create test database engine for each test."""
    # Use check_same_thread=False for SQLite to work with async
    engine = create_async_engine(
        TEST_DATABASE_URL,
        echo=False,
        connect_args={"check_same_thread": False},
    )

    # Create tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    # Cleanup
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    """Create a fresh database session for each test."""
    async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        yield session


@pytest.fixture
async def client(db_session):
    """Create test client with overridden database dependency."""

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    provider_client = MagicMock()

    def is_valid_device_id(value: str) -> bool:
        try:
            UUID(value)
        except ValueError:
            return False
        return True

    provider_client.is_valid_device_id.side_effect = is_valid_device_id
    app.state.dcim_client = provider_client
    app.state.cache_service = None

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-Auth-Request-Email": "test@example.com"},
    ) as ac:
        yield ac

    app.dependency_overrides.clear()
    app.state.dcim_client = None
    app.state.cache_service = None
