#!/bin/sh
# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
# Audit the actual runtime store without needing a shell/Python inside the image.
set -eu

if [ "$#" -ne 1 ]; then
    echo "Usage: $0 IMAGE" >&2
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

container_id=$(docker create "$1" /unused)
mkdir -p "$export_dir/etc/ssl" "$export_dir/usr/share"
docker cp "$container_id:/etc/ssl/certs" "$export_dir/etc/ssl/certs"
# Alpine's ca-certificates-bundle package can omit the individual source files.
if ! docker cp "$container_id:/usr/share/ca-certificates" "$export_dir/usr/share/ca-certificates" 2>"$export_dir/copy-error"; then
    case "$(cat "$export_dir/copy-error")" in
        *"Could not find the file /usr/share/ca-certificates in container"*) ;;
        *) cat "$export_dir/copy-error" >&2; exit 1 ;;
    esac
fi
uv run --no-project python "$script_dir/python-ca.py" --directory "$export_dir"
echo "$1: system CA bundle and individual trust anchors verified"
