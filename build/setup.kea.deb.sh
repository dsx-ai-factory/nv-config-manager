#!/usr/bin/env bash
# Configure the Kea 2.6 repository without executing a downloaded setup script.
set -euo pipefail

. /etc/os-release

key_file=$(mktemp)
trap 'rm -f "$key_file"' EXIT
curl -fsSL 'https://dl.cloudsmith.io/public/isc/kea-2-6/gpg.63D408891D8B8D01.key' -o "$key_file"
echo "0e14bebffdec7a47d6d265abf42766369b0f5dd21a9038b9b3ec1c964940837b  $key_file" | sha256sum -c -
gpg --batch --yes --dearmor -o /usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg "$key_file"
chmod 644 /usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg
printf 'deb [signed-by=/usr/share/keyrings/isc-kea-2-6-archive-keyring.gpg] https://dl.cloudsmith.io/public/isc/kea-2-6/deb/%s %s main\n' \
    "$ID" "$VERSION_CODENAME" > /etc/apt/sources.list.d/isc-kea-2-6.list
