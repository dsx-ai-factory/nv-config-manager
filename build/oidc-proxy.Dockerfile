# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Refresh OAuth2 Proxy's public CA bundle while awaiting a patched upstream
# release: https://github.com/oauth2-proxy/oauth2-proxy/issues/3578.
# The upstream binary and its Go toolchain are unchanged by this mitigation.
FROM quay.io/oauth2-proxy/oauth2-proxy:v7.15.5@sha256:8498b0d0ef0a7b29686414000a08aee467f02d0299c9ed1e006a8f33fc017916 AS upstream

FROM debian:13.7@sha256:2dc419c6d7fe3dad0a2f9fd469c34cc90b0f9ba642d8fde837fe3d7d34a0e7f1 AS trust-source
ARG APT_MIRROR_DEBIAN=""
ARG APT_MIRROR_GPG_KEY_URL=""
COPY build/python-ca.py build/refresh-rootfs-ca.py /tmp/
COPY build/configure-apt-mirror.sh /tmp/configure-apt-mirror.sh
RUN /tmp/configure-apt-mirror.sh "$APT_MIRROR_DEBIAN" "$APT_MIRROR_GPG_KEY_URL" debian && \
    apt-get update && \
    apt-get install --no-install-recommends -y ca-certificates python3 && \
    python3 /tmp/python-ca.py && \
    rm -rf /var/lib/apt/lists/*
COPY --from=upstream / /runtime/
RUN python3 /tmp/refresh-rootfs-ca.py /runtime

FROM scratch
COPY --from=trust-source /runtime/ /
# Preserve the upstream runtime contract while inheriting no old image layers.
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt
USER 65532
WORKDIR /home/nonroot
LABEL org.opencontainers.image.licenses=MIT \
      org.opencontainers.image.source=https://github.com/oauth2-proxy/oauth2-proxy
ENTRYPOINT ["/bin/oauth2-proxy"]
