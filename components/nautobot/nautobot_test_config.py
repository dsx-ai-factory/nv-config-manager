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
"""Test-only Nautobot config for the vendored nv_config_manager plugin suite.

Mirrors the essentials of the production ``nautobot_config`` but trims
``PLUGINS`` to just the plugin under test, so a one-shot test container does
not need the full set of third-party plugin configs (which come from Helm
overlays in real deployments). Used by ``make test-nautobot-plugin``.

Self-contained on purpose: Nautobot's CLI aliases the loaded config file
under the literal module name ``nautobot_config`` in ``sys.modules``, so any
``from nautobot_config import *`` would resolve back to this file rather than
the production one. Avoiding that footgun by inlining everything needed.
"""

import os

from nautobot.core.settings import *  # noqa: F401,F403
from nautobot.core.settings_funcs import parse_redis_connection

CACHES = {
    "default": {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": parse_redis_connection(redis_database=0),
        "TIMEOUT": 300,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "PASSWORD": os.getenv("NAUTOBOT_REDIS_PASSWORD", ""),
        },
    }
}

DATABASES = {
    "default": {
        "NAME": os.getenv("NAUTOBOT_DB_NAME", "nautobot"),
        "USER": os.getenv("NAUTOBOT_DB_USER", "nautobot"),
        "PASSWORD": os.getenv("NAUTOBOT_DB_PASSWORD", "nautobot"),
        "HOST": os.getenv("NAUTOBOT_DB_HOST", "localhost"),
        "PORT": os.getenv("NAUTOBOT_DB_PORT", ""),
        "CONN_MAX_AGE": int(os.getenv("NAUTOBOT_DB_TIMEOUT", "300")),
        "ENGINE": os.getenv("NAUTOBOT_DB_ENGINE", "django.db.backends.postgresql"),
    }
}

SECRET_KEY = os.environ.get("NAUTOBOT_SECRET_KEY", "plugin-test-suite-secret-not-for-prod")

ALLOWED_HOSTS = os.getenv("NAUTOBOT_ALLOWED_HOSTS", "config-manager.local localhost 127.0.0.1").split()

PLUGINS = ["nv_config_manager"]
PLUGINS_CONFIG = {
    "nv_config_manager": {"temporal_url": ""},
}
