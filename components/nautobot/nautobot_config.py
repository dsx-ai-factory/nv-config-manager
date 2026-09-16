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
"""Nautobot configuration settings for NVIDIA Config Manager external customers."""

# pylint: disable=wildcard-import,unused-wildcard-import
import os

from nautobot.core.events import register_event_broker
from nautobot.core.settings import *  # noqa F401,F403
from nautobot.core.settings_funcs import (
    is_truthy,
    parse_redis_connection,
    setup_structlog_logging,
)
from nautobot_broker_nats import NATSEventBroker

#########################
#                       #
#   SSO Authentication  #
#                       #
#########################

# Multi-provider JWT Authentication
# Handles all inbound auth:
#   - Browser users: OIDC JWT from NVConfigManagerAccessToken cookie (middleware + DRF)
#   - Internal services: SPIFFE JWT-SVID in Authorization: Bearer header
#   - External services: Third-party / other JWTs in Authorization: Bearer header
#   - API tokens: falls through to Nautobot's built-in TokenAuthentication
REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"] = [  # noqa: F405
    "nv_config_manager_auth.jwt_authentication.NVConfigManagerJWTAuthentication",
] + list(REST_FRAMEWORK.get("DEFAULT_AUTHENTICATION_CLASSES", []))  # noqa: F405

# JWT cookie middleware for Django web UI sessions (non-API pages).
# Must come after SessionMiddleware and AuthenticationMiddleware so the
# session/user are available, and **before** Nautobot's RemoteUserMiddleware so
# that JWT cookie login wins over header-based remote auth -- otherwise the
# RBAC sync in :func:`_get_or_create_user_from_claims` would never run because
# RemoteUserMiddleware authenticates the request first from the gateway's
# ``X-Auth-Request-*`` headers and JWTCookieMiddleware skips already-authed
# requests.
_JWT_COOKIE_MW = "nv_config_manager_auth.middleware.JWTCookieMiddleware"
_LOGOUT_REDIRECT_MW = "nv_config_manager_auth.middleware.LogoutRedirectMiddleware"
_REMOTE_USER_MW = "nautobot.core.middleware.RemoteUserMiddleware"
if _REMOTE_USER_MW in MIDDLEWARE:  # noqa: F405
    MIDDLEWARE.insert(MIDDLEWARE.index(_REMOTE_USER_MW), _JWT_COOKIE_MW)  # noqa: F405
else:
    MIDDLEWARE.append(_JWT_COOKIE_MW)  # noqa: F405
if _LOGOUT_REDIRECT_MW not in MIDDLEWARE:  # noqa: F405
    MIDDLEWARE.append(_LOGOUT_REDIRECT_MW)  # noqa: F405

#########################
#                       #
#   Required settings   #
#                       #
#########################

# The django-redis cache is used to establish concurrent locks using Redis.
CACHES = {
    "default": {
        "BACKEND": "django_prometheus.cache.backends.redis.RedisCache",
        "LOCATION": parse_redis_connection(redis_database=0),
        "TIMEOUT": 300,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            "PASSWORD": os.getenv("NAUTOBOT_REDIS_PASSWORD", ""),
        },
    }
}
CACHEOPS_REDIS = parse_redis_connection(redis_database=1)

# Database configuration
DATABASES = {
    "default": {
        "NAME": os.getenv("NAUTOBOT_DB_NAME", "nautobot"),  # Database name
        "USER": os.getenv("NAUTOBOT_DB_USER", ""),  # Database username
        "PASSWORD": os.getenv("NAUTOBOT_DB_PASSWORD", ""),  # Database password
        "HOST": os.getenv("NAUTOBOT_DB_HOST", "localhost"),  # Database server
        "PORT": os.getenv("NAUTOBOT_DB_PORT", ""),  # Database port (leave blank for default)
        "CONN_MAX_AGE": int(os.getenv("NAUTOBOT_DB_TIMEOUT", "300")),  # Database timeout
        "ENGINE": os.getenv(
            "NAUTOBOT_DB_ENGINE",
            "django_prometheus.db.backends.postgresql"
            if METRICS_ENABLED  # noqa: F405
            else "django.db.backends.postgresql",
        ),  # Database driver ("mysql" or "postgresql")
        "DISABLE_SERVER_SIDE_CURSORS": is_truthy(os.getenv("NAUTOBOT_DB_DISABLE_SERVER_SIDE_CURSORS", "False")),
    }
}

# Ensure proper Unicode handling for MySQL
if DATABASES["default"]["ENGINE"].endswith("mysql"):
    DATABASES["default"]["OPTIONS"] = {"charset": "utf8mb4"}

# Secret key for secure generation of random numbers and strings.
# Must be injected via environment (Kubernetes Secret / Helm values).
SECRET_KEY = os.environ.get("NAUTOBOT_SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("NAUTOBOT_SECRET_KEY environment variable is required but not set.")

#####################################
#                                   #
#   Optional Django core settings   #
#                                   #
#####################################

# FQDNs that are considered trusted origins for secure, cross-domain, requests such as HTTPS POST.
# Read additional origins from environment variable (comma-separated list)
_csrf_env = os.getenv("NAUTOBOT_CSRF_TRUSTED_ORIGINS", "")
CSRF_TRUSTED_ORIGINS = [
    "https://127.0.0.1",
    "https://localhost",
]
if _csrf_env:
    CSRF_TRUSTED_ORIGINS.extend([origin.strip() for origin in _csrf_env.split(",") if origin.strip()])

LOGOUT_REDIRECT_URL = os.getenv(
    "NAUTOBOT_LOGOUT_REDIRECT_URL",
    globals().get("LOGOUT_REDIRECT_URL", "/"),
)

# Set to True to enable server debugging. WARNING: Debugging introduces a substantial performance penalty and may reveal
# sensitive information about your installation. Only enable debugging while performing testing. Never enable debugging
# on a production system.
DEBUG = is_truthy(os.getenv("NAUTOBOT_DEBUG", "False"))

# Enable custom logging
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "normal": {
            "format": "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s :\n  %(message)s",
            "datefmt": "%H:%M:%S",
        },
        "verbose": {
            "format": (
                "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)-20s %(filename)-15s %(funcName)30s() :\n  %(message)s"
            ),
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "normal_console": {
            "level": "INFO",
            "class": "logging.StreamHandler",
            "formatter": "normal",
        },
        "verbose_console": {
            "level": "DEBUG",
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "loggers": {
        "django": {"handlers": ["normal_console"], "level": "INFO"},
        "nautobot": {
            "handlers": ["verbose_console" if DEBUG else "normal_console"],
            "level": "DEBUG" if DEBUG else "INFO",
        },
        # ``setup_structlog_logging`` only auto-wires loggers it discovers via
        # INSTALLED_APPS / MIDDLEWARE.  ``nv_config_manager_auth.jwt_authentication`` is
        # neither, so its log records (Promoted/Demoted/Created user) would
        # otherwise be silently dropped under uwsgi.  Pin the parent so both
        # the middleware and the JWT authentication module emit at INFO.
        "nv_config_manager_auth": {
            "handlers": ["verbose_console" if DEBUG else "normal_console"],
            "level": "DEBUG" if DEBUG else "INFO",
            "propagate": False,
        },
    },
}

# Enable the following to setup structlog logging for Nautobot.
setup_structlog_logging(
    LOGGING,
    INSTALLED_APPS,  # noqa F405
    MIDDLEWARE,  # noqa F405
    log_level="DEBUG" if DEBUG else "INFO",
    debug_db=False,  # Set to True to log all database queries
    plain_format=True,  # Set to True to use human-readable structlog format over JSON
)

###################################################################
#                                                                 #
#   Optional settings specific to Nautobot and its related apps   #
#                                                                 #
###################################################################

# Send anonymized installation metrics when `nautobot-server post_upgrade` command is run.
INSTALLATION_METRICS_ENABLED = False

# Jobs root directory - where Nautobot jobs are loaded from
JOBS_ROOT = os.getenv("NAUTOBOT_JOBS_ROOT", "/opt/nautobot/jobs")

# Enable installed plugins. Only the required plugins for NVIDIA Config Manager external customers.
PLUGINS = [
    "nautobot_fsus",
    "nv_config_manager",
    "nautobot_firewall_models",
    "nautobot_design_builder",
    "nautobot_nvdatamodels",
    "nautobot_bgp_models",
    "nautobot_app_overlays",
    "nautobot_app_routing",
]

# Plugins configuration settings
PLUGINS_CONFIG = {
    "nautobot_bgp_models": {},
    "nv_config_manager": {"temporal_url": os.getenv("NV_CONFIG_MANAGER_TEMPORAL_URL", "")},
}

# Configure NATS Event Broker
if "NATS_HOST" in os.environ:
    connect = {}

    # Optional path to a credentials file.
    if "NATS_CRED" in os.environ:
        connect["user_credentials"] = os.environ["NATS_CRED"]
    # Use username/password authentication if provided
    elif "NATS_USER" in os.environ and "NATS_PASSWORD" in os.environ:
        connect["user"] = os.environ["NATS_USER"]
        connect["password"] = os.environ["NATS_PASSWORD"]

    register_event_broker(
        NATSEventBroker(
            servers=os.environ["NATS_HOST"],
            stream="nautobot",
            **connect,
        )
    )

# Custom message to display on 4xx and 5xx error pages
SUPPORT_MESSAGE = """
If further assistance is required, please contact your NVIDIA Config Manager support team.
"""
