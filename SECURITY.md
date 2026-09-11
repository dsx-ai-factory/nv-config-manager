# Security Policy

## Reporting a Vulnerability
If you need to report a security issue, please use the appropriate contact points outlined below. **Please do not report security vulnerabilities through GitHub.**
## Reporting Potential Security Vulnerability in an NVIDIA Product

To report a potential security vulnerability in any NVIDIA product:
- Web: [Security Vulnerability Submission Form](https://www.nvidia.com/object/submit-security-vulnerability.html)

- E-Mail: psirt@nvidia.com
	 - We encourage you to use the following PGP key for secure email communication: [NVIDIA public PGP Key for communication](https://www.nvidia.com/en-us/security/pgp-key)
	 - Please include the following information:
	 - Product/Driver name and version/branch that contains the vulnerability
	 - Type of vulnerability (code execution, denial of service, buffer overflow, etc.)
	 - Instructions to reproduce the vulnerability
	 - Proof-of-concept or exploit code
	 - Potential impact of the vulnerability, including how an attacker could exploit the vulnerability

While NVIDIA currently does not have a bug bounty program, we do offer acknowledgement when an externally reported security issue is addressed under our coordinated vulnerability disclosure policy. Please visit our [Product Security Incident Response Team (PSIRT)](https://www.nvidia.com/en-us/security/psirt-policies/) policies page for more information.

## NVIDIA Product Security

For all security-related concerns, please visit NVIDIA's Product Security portal at https://www.nvidia.com/en-us/security

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.2.x   | Yes       |
| < 1.2   | No        |

## Trust Model

NVIDIA Config Manager is a **Kubernetes-native infrastructure management platform**.
The primary trust boundary is the cluster network.

### Assumptions

- **Intra-namespace communication is secured with SPIFFE.** The gold standard
  deployment uses SPIFFE JWT-SVIDs for service-to-service authentication
  within the namespace. Each workload receives a cryptographic identity via
  SPIRE or Teleport, and API services validate inbound JWTs against the
  SPIFFE trust bundle. SPIFFE is fully supported, tested, and recommended
  as the primary intra-cluster security mechanism. Deployments without
  SPIFFE fall back to NetworkPolicy namespace isolation and gateway-mediated
  auth.
- **Gateway mediates external access.** All user-facing traffic enters
  through a supported Gateway API controller and the OIDC/JWT authentication proxy.
  Internal APIs delegate authentication to the gateway and SPIFFE layers
  for flexibility across deployment environments.
- **Secrets are injected, never hardcoded.** All secrets (database passwords,
  API tokens, cookie secrets) must be provided via Kubernetes Secrets or
  External Secrets Operator (ESO). The platform will fail to start if
  required secrets are missing.
- **Network devices use self-signed certificates.** Switches, BMCs, and other
  managed hardware typically ship with self-signed TLS certificates. TLS
  verification is intentionally disabled for device management connections.
  This remains separate from device identity and telemetry certificates, which
  can be issued during ZTP and rotated nightly.
- **Sensitive ZTP downloads default to SFTP.** The built-in Cumulus and NVOS
  templates retrieve rendered configuration and device certificates through
  the source-IP-authorized SFTP proxy. HTTP and HTTPS endpoints remain
  available for compatibility, but the default templates do not retrieve
  plaintext configuration secrets or private keys over HTTP. The Cumulus HTTP
  bootstrap script still contains the salted `sha512-crypt` verifier for its
  temporary breakglass account, not the plaintext breakglass password.

### Authentication Layers

| Layer | Mechanism | Scope |
|-------|-----------|-------|
| Gateway (external) | OIDC / JWT / mTLS | All user-facing routes |
| Service-to-service | SPIFFE JWT-SVIDs | Internal API calls (recommended) |
| Network isolation | Kubernetes NetworkPolicy | Namespace-level ingress isolation |
| Device access | IP auth + prefix-based ACLs (SG / NetworkPolicy) | ZTP/DHCP LoadBalancer endpoints |
| Nautobot | Django auth + JWT plugin | Nautobot UI and API |

## Container Security

- **Base images:** NVIDIA distroless images (`nvcr.io/nvidia/distroless/python`,
  `nvcr.io/nvidia/distroless/node`, `nvcr.io/nvidia/distroless/go`) — minimal
  attack surface with no shell, package manager, or unnecessary utilities.
- **Non-root runtime:** NVIDIA distroless images define the `nvs` user
  (UID 1000, GID 1000). Kubernetes `securityContext` enforces
  `runAsNonRoot: true`, `runAsUser: 1000`, `allowPrivilegeEscalation: false`,
  and `capabilities.drop: [ALL]` across all application containers.
- **Exception — Kea DHCP:** The Kea container uses an Ubuntu base and runs as
  root because DHCP requires binding UDP port 67. This is an accepted risk;
  Kubernetes-level seccomp profiles are applied.
- **Exception — Airgapped image loader:** The image-loader DaemonSet requires
  privileged access to import container images into node containerd. It runs
  in a dedicated ephemeral namespace with `automountServiceAccountToken: false`.
- **CVE scanning:** All images are scanned during CI.
  Dependency versions are pinned with GHSA comments in `pyproject.toml`.

## Dependency Management

- Python dependencies are managed via `uv` with a lockfile (`uv.lock`) for
  reproducible builds.
- Minimum versions are pinned with security advisory references, e.g.:
  - `aiohttp >= 3.13.4` (GHSA-w2fm, GHSA-p998, GHSA-c427, GHSA-m5qp)
  - `cryptography >= 46.0.7` (GHSA-r6ph, GHSA-p423)
  - `requests >= 2.33.0` (GHSA-gc5v)
  - `urllib3 >= 2.6.0` (GHSA-gm62, GHSA-2xpw)
  - `pynacl >= 1.6.2` (GHSA-mrfv)


## Accepted Risks

The following items were reviewed during the security audit and accepted:

1. **TLS verification disabled for device management.** Network devices
   (switches, BMCs) use self-signed management certificates. ZTP can install
   device identity and telemetry certificates, but does not yet replace every
   platform's management-plane certificate and trust chain.

2. **The Kea DHCP container runs as root.** DHCP requires binding UDP port 67.
   The kea-admin migration container runs as UID/GID 1000 and is not part of
   this exception.

3. **Privileged image-loader DaemonSet.** Required for airgapped deployments
   to import images into node containerd. Runs in a dedicated namespace
   that is deleted after use.

4. **NetworkPolicy egress unrestricted.** NVIDIA Config Manager services require egress to
   many deployment-specific destinations. Ingress namespace isolation is the
   primary network control.

5. **CORS wildcard methods/headers.** `allow_origins` is a strict allowlist
   from Helm configuration, not wildcarded. Wildcard methods/headers with
   restricted origins is standard practice.

6. **Full Python stack traces in workflow stage responses.** When a workflow
   stage fails, the traceback is included in the API response so that
   operators can quickly diagnose issues (e.g., device unreachable, SSH
   timeout) without needing log access. This is intentional for usability
   but does expose internal file paths and library details. A future
   improvement will introduce structured `UserActionableError` messages
   that provide clean, actionable diagnostics while keeping full tracebacks
   server-side only. This requires coordinated changes across workflow
   error handling and the UI stage detail view.

## Out of Scope

- Internal-only API surfaces behind NetworkPolicy (config-store, render,
  codec server) — authentication is handled at the gateway layer.
- Test credentials in `src/tests/` — intentional fixtures for unit/integration
  tests, never deployed.
