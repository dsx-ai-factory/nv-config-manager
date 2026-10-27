#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Audit the actual runtime store without needing a shell/Python inside the image.
set -eu

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || { [ "$#" -eq 2 ] && [ "$2" != --node ]; }; then
    echo "Usage: $0 IMAGE [--node]" >&2
    exit 2
fi

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
export_dir=$(mktemp -d)
container_id=""
cleanup() {
    if [ -n "$container_id" ]; then docker rm "$container_id" >/dev/null; fi
    rm -rf "$export_dir"
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$1")
echo "Auditing $1 ($platform)"
container_id=$(docker create --platform "$platform" "$1" /unused)
mkdir -p "$export_dir/etc/ssl" "$export_dir/usr/share" "$export_dir/usr/local/share"
docker cp "$container_id:/etc/ssl/certs" "$export_dir/etc/ssl/certs"
# Include locally installed anchors; Alpine can omit either source directory.
# Only Docker's known missing-path error is optional; unknown errors fail closed.
for ca_dir in /usr/share/ca-certificates /usr/local/share/ca-certificates; do
    if ! docker cp "$container_id:$ca_dir" "$export_dir$ca_dir" 2>"$export_dir/copy-error"; then
        case "$(cat "$export_dir/copy-error")" in
            *"Could not find the file $ca_dir in container"*) ;;
            *) cat "$export_dir/copy-error" >&2; exit 1 ;;
        esac
    fi
done
uv run --no-project --python python3 python "$script_dir/python-ca.py" --directory "$export_dir"
if [ "${2:-}" = --node ]; then
    docker run --rm --network none --read-only --cap-drop ALL \
        --security-opt no-new-privileges --platform "$platform" --entrypoint node "$1" \
        -p 'require("node:tls").rootCertificates.join("\n")' > "$export_dir/node-roots.pem"
    uv run --no-project --python python3 python "$script_dir/python-ca.py" --bundle "$export_dir/node-roots.pem"
    echo "$1: Node bundled CA store verified"
fi
echo "$1: system CA bundle and individual trust anchors verified"
