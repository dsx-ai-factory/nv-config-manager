# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Refresh OAuth2 Proxy's public CA bundle while awaiting a patched upstream
# release: https://github.com/oauth2-proxy/oauth2-proxy/issues/3578.
# The upstream binary and its Go toolchain are unchanged by this mitigation.
FROM debian:13.7@sha256:2dc419c6d7fe3dad0a2f9fd469c34cc90b0f9ba642d8fde837fe3d7d34a0e7f1 AS trust-source
ARG APT_MIRROR_DEBIAN=""
ARG APT_MIRROR_GPG_KEY_URL=""
COPY build/python-ca.py /tmp/python-ca.py
COPY build/configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN /tmp/configure-apt-mirror.sh "$APT_MIRROR_DEBIAN" "$APT_MIRROR_GPG_KEY_URL" debian && \
    apt-get update && \
    apt-get install --no-install-recommends -y ca-certificates python3 && \
    python3 /tmp/python-ca.py && \
    rm -rf /var/lib/apt/lists/*

# This exact upstream image has only the combined CA bundle. The existing
# final-image trust audit checks the resulting image before publication.
FROM quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916
COPY --from=trust-source /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
