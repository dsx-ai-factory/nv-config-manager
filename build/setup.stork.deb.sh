#!/usr/bin/env bash
#
# Setup script for ISC Stork agent on Debian/Ubuntu
# Source: https://dl.cloudsmith.io/public/isc/stork/
#

set -euo pipefail

echo "Setting up ISC Stork repository for Debian/Ubuntu..."

# Install required packages for adding repositories
apt-get update
apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    gnupg \
    apt-transport-https

# Detect OS version
. /etc/os-release

# Map Ubuntu Noble (24.04) to Jammy (22.04) since Stork may not have Noble packages yet
STORK_CODENAME="${VERSION_CODENAME}"
if [[ "${VERSION_CODENAME}" = "noble" ]]; then
    echo "Ubuntu Noble detected, using Jammy repository (compatible packages)..."
    STORK_CODENAME="jammy"
fi

# Configure the signed repository without executing a downloaded setup script.
key_file=$(mktemp)
trap 'rm -f "$key_file"' EXIT
curl -fsSL 'https://dl.cloudsmith.io/public/isc/stork/gpg.77F64EC28053D1FB.key' -o "$key_file"
echo "ba7714baafbb9764f0645cdf996e297f8fe7bf37d909c912ca593136f1c066dc  $key_file" | sha256sum -c -
gpg --batch --yes --dearmor -o /usr/share/keyrings/isc-stork-archive-keyring.gpg "$key_file"
chmod 644 /usr/share/keyrings/isc-stork-archive-keyring.gpg
printf 'deb [signed-by=/usr/share/keyrings/isc-stork-archive-keyring.gpg] https://dl.cloudsmith.io/public/isc/stork/deb/%s %s main\n' \
    "$ID" "$STORK_CODENAME" > /etc/apt/sources.list.d/isc-stork.list
apt-get update

echo "Stork repository setup complete!"
