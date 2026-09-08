{{/*
Expand the name of the chart.
*/}}
{{- define "nv-config-manager.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "nv-config-manager.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "nv-config-manager.labels" -}}
helm.sh/chart: {{ include "nv-config-manager.chart" . }}
{{ include "nv-config-manager.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | default .Chart.Version | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: nv-config-manager
{{- end }}

{{/*
Resolve an NVIDIA Config Manager image tag. Release pipelines may set a
per-image tag; checked-in values fall back to the chart version so the source
tree has only one version to maintain.
*/}}
{{- define "nv-config-manager.imageTag" -}}
{{- .image.tag | default .root.Chart.AppVersion | default .root.Chart.Version -}}
{{- end }}

{{/*
Resolve a full NVIDIA Config Manager image reference. A digest pin
(sha256:<hex>, set by deploy automation) takes precedence over any tag and
renders repository@digest; otherwise fall back to repository:tag with the
imageTag chart-version fallback. Takes the same dict as imageTag:
(dict "root" . "image" .Values.global.images.<name>)
*/}}
{{- define "nv-config-manager.image" -}}
{{- if .image.digest -}}
{{- .image.repository }}@{{ .image.digest -}}
{{- else -}}
{{- .image.repository }}:{{ include "nv-config-manager.imageTag" . -}}
{{- end -}}
{{- end }}

{{/*
Resolve the Temporal gRPC endpoint.  When the project-owned server is
disabled, a user-managed Temporal endpoint is required instead.
*/}}
{{- define "nv-config-manager.temporalGrpcAddress" -}}
{{- if .Values.temporal.server.enabled -}}
{{- $temporalName := include "nv-config-manager.componentName" (dict "root" . "component" "temporal") -}}
{{ printf "%s-frontend-service.%s.svc.cluster.local:%v" $temporalName .Values.global.namespace .Values.temporal.services.frontend.port }}
{{- else -}}
{{- required "temporal.client.address is required when temporal.server.enabled=false" .Values.temporal.client.address -}}
{{- end -}}
{{- end }}

{{/*
Render a Temporal TLS server name as a single raw INI value.  Helm's `quote`
helper produces YAML quotes, which ConfigParser preserves as part of the TLS
domain name.  Limit the value to DNS-name/IP-literal characters so direct
Helm values cannot add another INI setting.
*/}}
{{- define "nv-config-manager.temporalTLSServerName" -}}
{{- $serverName := .Values.temporal.client.tls.serverName | default "" -}}
{{- if $serverName -}}
{{- if not (regexMatch `^[A-Za-z0-9:.-]+$` $serverName) -}}
{{- fail "temporal.client.tls.serverName may contain only DNS-name or IP-literal characters" -}}
{{- end -}}
{{- $serverName -}}
{{- end -}}
{{- end }}

{{- define "nv-config-manager.temporalClientTLSVolumeMount" -}}
{{- if .Values.temporal.client.tls.enabled }}
- name: temporal-client-tls
  mountPath: /var/run/secrets/temporal-client-tls
  readOnly: true
{{- end }}
{{- end }}

{{- define "nv-config-manager.temporalClientTLSVolume" -}}
{{- if .Values.temporal.client.tls.enabled }}
- name: temporal-client-tls
  secret:
    secretName: {{ required "temporal.client.tls.secretName is required when TLS is enabled" .Values.temporal.client.tls.secretName }}
{{- end }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "nv-config-manager.selectorLabels" -}}
app.kubernetes.io/name: {{ include "nv-config-manager.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Workload ServiceAccount (Vault K8s/JWT auth binds to this identity; must match Vault role).
*/}}
{{- define "nv-config-manager.serviceAccountName" -}}
{{- .Values.global.serviceAccountName | default "vault-access-sa" -}}
{{- end }}

{{/*
Deployment rollout strategy.
Pass root and, optionally, strategy. Global strategy wins when set so local
overrides can switch every Deployment to Recreate in one place.
*/}}
{{- define "nv-config-manager.deploymentStrategy" -}}
{{- $strategy := .strategy | default dict -}}
{{- if .root.Values.global.deploymentStrategy -}}
{{- $strategy = .root.Values.global.deploymentStrategy -}}
{{- end -}}

{{- if $strategy }}
{{- $type := $strategy.type | default "RollingUpdate" -}}
strategy:
  type: {{ $type }}
{{- if eq $type "RollingUpdate" }}
{{- $rollingUpdate := $strategy.rollingUpdate | default dict }}
  rollingUpdate:
    maxSurge: {{ $rollingUpdate.maxSurge | default "25%" }}
    maxUnavailable: {{ $rollingUpdate.maxUnavailable | default 0 }}
{{- end }}
{{- end }}
{{- end }}

{{/* HTTPRoute parent reference for chart-managed or shared Gateways. */}}
{{- define "nv-config-manager.gatewayParentRef" -}}
- name: {{ .Values.gateway.name }}
  namespace: {{ .Values.gateway.namespace | default .Values.global.namespace }}
  {{- with .Values.gateway.sectionName }}
  sectionName: {{ . }}
  {{- end }}
{{- end -}}

{{/*
Resolve the Gateway API controller selected by the chart.

`ingress.type` predates the Gateway API migration and remains the canonical
controller selector so existing values files do not need a no-op rename.
`gateway.type` is accepted as a transition alias for configurations generated
by early Gateway API releases. Setting both to different values is ambiguous.
*/}}
{{- define "nv-config-manager.gatewayControllerType" -}}
{{- $ingress := .Values.ingress | default dict -}}
{{- $gateway := .Values.gateway | default dict -}}
{{- $ingressType := get $ingress "type" | default "" -}}
{{- $gatewayType := get $gateway "type" | default "" -}}
{{- if and $ingressType $gatewayType (ne $ingressType $gatewayType) -}}
{{- fail (printf "ingress.type (%q) and gateway.type (%q) must match when both are set" $ingressType $gatewayType) -}}
{{- end -}}
{{- coalesce $ingressType $gatewayType "envoyGateway" -}}
{{- end -}}

{{/*
Generate the base hostname for the gateway
*/}}
{{- define "nv-config-manager.hostname" -}}
{{- .Values.gateway.baseHostname }}
{{- end }}

{{/*
Get the Nautobot server URL based on whether local deployment is enabled
(internal URL for in-cluster API calls)
*/}}
{{- define "nv-config-manager.nautobotServer" -}}
{{- if .Values.externalServices.nautobot.local -}}
{{- $nautobotName := include "nv-config-manager.componentName" (dict "root" . "component" "nautobot") -}}
http://{{ $nautobotName }}
{{- else -}}
{{- required "externalServices.nautobot.server is required when nautobot.local=false" .Values.externalServices.nautobot.server -}}
{{- end -}}
{{- end -}}

{{/*
Get the Nautobot public URL for user-facing links (e.g. device metadata in config-store API).
When local=true this is the gateway hostname; when local=false this is the external server URL.
*/}}
{{- define "nv-config-manager.nautobotPublicUrl" -}}
{{- if .Values.externalServices.nautobot.local -}}
https://{{ tpl .Values.nautobot.gateway.hostname . }}
{{- else -}}
{{- required "externalServices.nautobot.server is required when nautobot.local=false" .Values.externalServices.nautobot.server -}}
{{- end -}}
{{- end -}}

{{/*
Resolve the provider-neutral DCIM endpoint. External providers must state an
endpoint directly; Nautobot inherits the historical configuration by default.
*/}}
{{- define "nv-config-manager.dcimServer" -}}
{{- if .Values.dcim.server -}}
{{- tpl .Values.dcim.server . -}}
{{- else if eq (.Values.dcim.provider | default "nautobot-2x") "nautobot-2x" -}}
{{- include "nv-config-manager.nautobotServer" . -}}
{{- else -}}
{{- fail "dcim.server is required when dcim.provider is not nautobot-2x" -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.dcimPublicUrl" -}}
{{- if .Values.dcim.publicUrl -}}
{{- tpl .Values.dcim.publicUrl . -}}
{{- else if eq (.Values.dcim.provider | default "nautobot-2x") "nautobot-2x" -}}
{{- include "nv-config-manager.nautobotPublicUrl" . -}}
{{- else -}}
{{- include "nv-config-manager.dcimServer" . -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.dcimDisplayName" -}}
{{- if .Values.dcim.displayName -}}
{{- .Values.dcim.displayName -}}
{{- else if eq (.Values.dcim.provider | default "nautobot-2x") "nautobot-2x" -}}
Nautobot
{{- else -}}
{{- .Values.dcim.provider | default "DCIM" -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.dcimVerify" -}}
{{- $verify := .Values.dcim.verify -}}
{{- if or (kindIs "bool" $verify) (ne $verify "") -}}
{{- $verify -}}
{{- else -}}
{{- .Values.externalServices.nautobot.verify -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.dcimCacheRefreshInterval" -}}
{{- .Values.dcim.cacheRefreshInterval | default .Values.externalServices.nautobot.cacheRefreshInterval -}}
{{- end -}}

{{- define "nv-config-manager.dcimCacheTtl" -}}
{{- .Values.dcim.cacheTtl | default .Values.externalServices.nautobot.cacheTtl -}}
{{- end -}}

{{/*
Get the Redis host based on whether local deployment is enabled
*/}}
{{- define "nv-config-manager.redisHost" -}}
{{- if .Values.externalServices.redis.local -}}
{{- $redisName := include "nv-config-manager.componentName" (dict "root" . "component" "redis") -}}
{{ $redisName }}-master
{{- else -}}
{{- required "externalServices.redis.host is required when redis.local=false" .Values.externalServices.redis.host -}}
{{- end -}}
{{- end -}}

{{/*
Get the NATS server URL based on whether local deployment is enabled
*/}}
{{- define "nv-config-manager.natsServer" -}}
{{- if or .Values.externalServices.nats.local .Values.nautobotNats.enabled -}}
{{- $natsName := include "nv-config-manager.componentName" (dict "root" . "component" "nats") -}}
nats://{{ $natsName }}:4222
{{- else -}}
{{- required "externalServices.nats.server is required when nats.local=false" .Values.externalServices.nats.server -}}
{{- end -}}
{{- end -}}

{{/*
Nautobot common labels
*/}}
{{- define "nv-config-manager.nautobot.labels" -}}
{{ include "nv-config-manager.labels" . }}
app.kubernetes.io/component: nautobot
{{- end -}}

{{/* Bundled NATS common labels. The historical helper name is retained for compatibility. */}}
{{- define "nv-config-manager.nautobot-nats.labels" -}}
{{ include "nv-config-manager.labels" . }}
app.kubernetes.io/component: nats
{{- end -}}

{{/*
=============================================================================
SPIFFE/Envoy Sidecar Helpers
=============================================================================
Supports both SPIRE and Teleport as SPIFFE providers.
- SPIRE: Uses CSI driver (csi.spiffe.io) to mount workload API socket
- Teleport: Uses hostPath to access Teleport Machine ID socket

Authentication modes:
- jwt: JWT-SVID based authentication (recommended, simpler)
- mtls: X.509-SVID based mutual TLS
=============================================================================
*/}}

{{/*
Get the full SPIFFE socket path (mount path + socket file)
*/}}
{{- define "nv-config-manager.spiffe.socketPath" -}}
{{- $spiffe := .Values.spiffe | default dict -}}
{{- $socket := index $spiffe "socket" | default dict -}}
{{- $mountPath := index $socket "mountPath" | default "/spiffe-workload-api" -}}
{{- $socketFile := index $socket "socketFile" | default "spire-agent.sock" -}}
{{- printf "%s/%s" $mountPath $socketFile -}}
{{- end -}}

{{/*
Return "true" if spiffe is enabled (nil-safe). Use in conditionals.
*/}}
{{- define "nv-config-manager.spiffe.enabled" -}}
{{- $spiffe := .Values.spiffe | default dict }}
{{- if eq (index $spiffe "enabled") true }}
true
{{- end }}
{{- end -}}

{{/*
SPIFFE pod label - added alongside the annotation on every pod that opts in
to SPIFFE identity.  Used as the ClusterSPIFFEID podSelector so the selector
exactly matches the set of pods that have the spiffe.io/spiffe-id annotation,
eliminating render failures for pods like CNPG clusters that share the
namespace but are not nv-config-manager workloads.
Only emitted for the SPIRE provider; Teleport uses join-token registration.
*/}}
{{- define "nv-config-manager.spiffe.podLabels" -}}
{{- if include "nv-config-manager.spiffe.enabled" . }}
{{- $provider := index (.Values.spiffe | default dict) "provider" | default "spire" }}
{{- if eq $provider "spire" }}
spiffe.nv-config-manager.io/inject: "true"
{{- end }}
{{- end }}
{{- end -}}

{{/*
SPIFFE pod annotations for workload registration
- For SPIRE: Uses spiffe.io annotations for spire-controller-manager
- For Teleport: No annotations needed (uses join token based registration)
Pass app (pod's app label value); defaults to "nv-config-manager".
*/}}
{{- define "nv-config-manager.spiffe.annotations" -}}
{{- if include "nv-config-manager.spiffe.enabled" .root }}
{{- $spiffe := .root.Values.spiffe | default dict }}
{{- $provider := index $spiffe "provider" | default "spire" }}
{{- $global := .root.Values.global | default dict }}
{{- $app := .app | default "nv-config-manager" }}
{{- if eq $provider "spire" }}
spiffe.io/spiffe-id: "spiffe://{{ index $spiffe "trustDomain" }}/ns/{{ index $global "namespace" }}/sa/{{ $app }}"
{{- else if eq $provider "teleport" }}
teleport.dev/workload-identity: "true"
{{- end }}
{{- end }}
{{- end -}}

{{/*
SPIFFE volume mounts for main container (if app needs direct SPIFFE access)
*/}}
{{- define "nv-config-manager.spiffe.volumeMounts" -}}
{{- if include "nv-config-manager.spiffe.enabled" . }}
{{- $socket := index (.Values.spiffe | default dict) "socket" | default dict }}
- name: spiffe-workload-api
  mountPath: {{ index $socket "mountPath" | default "/spiffe-workload-api" }}
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Auth sidecar -- renders spiffe-helper for outbound JWT-SVID fetching.
API pods that receive inbound SPIFFE calls also get the Workload API socket
mounted so py-spiffe can validate incoming JWTs.
Usage: {{ include "nv-config-manager.authSidecar" (dict "root" . "serviceName" "render-service" "allowedGroups" (list "group1")) | nindent 6 }}
*/}}
{{- define "nv-config-manager.authSidecar" -}}
{{- $useExternalJwt := .root.Values.gateway.auth.jwt.enabled }}
{{- $spiffe := .root.Values.spiffe | default dict }}
{{- $useSpiffe := include "nv-config-manager.spiffe.enabled" .root }}
{{- $useSso := .root.Values.oidc.enabled }}
{{- $authMode := index $spiffe "authMode" | default "jwt" }}
{{- $spiffeHelper := index $spiffe "helper" | default dict }}
{{- $spiffeHelperImage := index $spiffeHelper "image" | default dict }}
{{- $spiffeSocket := index $spiffe "socket" | default dict }}
{{- $spiffeEnvoy := index $spiffe "envoy" | default dict }}
{{- $spiffeEnvoyImage := index $spiffeEnvoy "image" | default dict }}
{{- if or $useExternalJwt $useSso (and $useSpiffe (eq $authMode "jwt")) }}
{{- /* Use unified sidecar for external JWT or SPIFFE JWT mode */ -}}
{{- if and $useSpiffe (eq $authMode "jwt") }}
# SPIFFE Helper -- fetches JWT-SVIDs for outbound service-to-service calls
- name: spiffe-helper
  image: {{ index $spiffeHelperImage "repository" | default "ghcr.io/spiffe/spiffe-helper" }}:{{ index $spiffeHelperImage "tag" | default "0.8.0" }}
  imagePullPolicy: {{ index $spiffeHelperImage "pullPolicy" | default "IfNotPresent" }}
  args:
    - -config
    - /etc/spiffe-helper/helper.conf
  env:
    - name: SPIFFE_ENDPOINT_SOCKET
      value: "unix://{{ include "nv-config-manager.spiffe.socketPath" .root }}"
  resources:
    {{- if index $spiffeHelper "resources" }}
    {{- toYaml (index $spiffeHelper "resources") | nindent 4 }}
    {{- else }}
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 100m
      memory: 64Mi
    {{- end }}
  volumeMounts:
    - name: spiffe-workload-api
      mountPath: {{ index $spiffeSocket "mountPath" | default "/spiffe-workload-api" }}
      readOnly: true
    - name: spiffe-helper-config
      mountPath: /etc/spiffe-helper
      readOnly: true
    - name: spiffe-jwt-svid
      mountPath: /var/run/secrets/spiffe
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 1000
    capabilities:
      drop:
        - ALL
{{- end }}
{{- end }}
{{- end -}}

{{/*
Auth sidecar volumes -- SPIFFE Workload API socket, helper config, JWT emptyDir.
No Envoy config volume needed (JWT validation is in-app).
Usage: {{ include "nv-config-manager.authSidecar.volumes" (dict "root" .) | nindent 6 }}
*/}}
{{- define "nv-config-manager.authSidecar.volumes" -}}
{{- $spiffe := .root.Values.spiffe | default dict }}
{{- $useSpiffe := include "nv-config-manager.spiffe.enabled" .root }}
{{- $authMode := index $spiffe "authMode" | default "jwt" }}
{{- if and $useSpiffe (eq $authMode "jwt") }}
{{- $provider := index $spiffe "provider" | default "spire" }}
{{- $spiffeMock := index $spiffe "mock" | default dict }}
{{- $spiffeSpire := index $spiffe "spire" | default dict }}
{{- $spiffeSocket := index $spiffe "socket" | default dict }}
- name: spiffe-workload-api
{{- if index $spiffeMock "enabled" }}
  emptyDir: {}
{{- else if eq $provider "spire" }}
  csi:
    driver: {{ index $spiffeSpire "csiDriver" | default "csi.spiffe.io" | quote }}
    readOnly: true
{{- else if eq $provider "teleport" }}
  hostPath:
    path: {{ index $spiffeSocket "hostPath" | default "/var/run/teleport" }}
    type: Directory
{{- end }}
- name: spiffe-helper-config
  configMap:
    name: spiffe-helper-config
- name: spiffe-jwt-svid
  emptyDir:
    medium: Memory
    sizeLimit: 1Mi
{{- end }}
{{- end -}}

{{/*
Auth sidecar inbound port (for Service targetPort, Envoy config). Default 8443.
Nil-safe when .Values.spiffe or .Values.spiffe.envoy is missing.
Usage: {{ include "nv-config-manager.authSidecar.inboundPort" . }}
*/}}
{{- define "nv-config-manager.authSidecar.inboundPort" -}}
9000
{{- end -}}

{{/*
SPIFFE client sidecar -- spiffe-helper only (no envoy).
Used by caller pods (consumers, workers, refresh jobs) that need to present
JWT-SVIDs when calling API services but don't receive inbound requests.
Usage: {{- include "nv-config-manager.spiffeClientSidecar" (dict "root" .) | nindent 8 }}
*/}}
{{- define "nv-config-manager.spiffeClientSidecar" -}}
{{- $spiffe := .root.Values.spiffe | default dict }}
{{- if and (index $spiffe "enabled") (eq (index $spiffe "authMode" | default "jwt") "jwt") }}
- name: spiffe-helper
  image: {{ (index $spiffe "helper" | default dict).image.repository | default "ghcr.io/spiffe/spiffe-helper" }}:{{ (index $spiffe "helper" | default dict).image.tag | default "0.8.0" }}
  imagePullPolicy: {{ (index $spiffe "helper" | default dict).image.pullPolicy | default "IfNotPresent" }}
  args:
    - -config
    - /etc/spiffe-helper/helper.conf
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 100m
      memory: 64Mi
  volumeMounts:
    - name: spiffe-workload-api
      mountPath: {{ (index $spiffe "socket" | default dict).mountPath | default "/spiffe-workload-api" }}
      readOnly: true
    - name: spiffe-helper-config
      mountPath: /etc/spiffe-helper
      readOnly: true
    - name: spiffe-jwt-svid
      mountPath: /var/run/secrets/spiffe
  securityContext:
    allowPrivilegeEscalation: false
    readOnlyRootFilesystem: true
    runAsNonRoot: true
    runAsUser: 1000
    capabilities:
      drop:
        - ALL
{{- end }}
{{- end -}}

{{/*
SPIFFE client volumes -- socket + helper config + JWT emptyDir.
Used alongside spiffeClientSidecar for caller pods that don't need envoy.
Usage: {{- include "nv-config-manager.spiffeClientVolumes" (dict "root" .) | nindent 6 }}
*/}}
{{- define "nv-config-manager.spiffeClientVolumes" -}}
{{- $spiffe := .root.Values.spiffe | default dict }}
{{- if and (index $spiffe "enabled") (eq (index $spiffe "authMode" | default "jwt") "jwt") }}
{{- $provider := index $spiffe "provider" | default "spire" }}
- name: spiffe-workload-api
{{- if eq $provider "spire" }}
  csi:
    driver: {{ index (index $spiffe "spire" | default dict) "csiDriver" | default "csi.spiffe.io" | quote }}
    readOnly: true
{{- else if eq $provider "teleport" }}
  hostPath:
    path: {{ index (index $spiffe "socket" | default dict) "hostPath" | default "/var/run/teleport" }}
    type: Directory
{{- end }}
- name: spiffe-helper-config
  configMap:
    name: spiffe-helper-config
- name: spiffe-jwt-svid
  emptyDir:
    medium: Memory
    sizeLimit: 1Mi
{{- end }}
{{- end -}}

{{/*
Volume mounts for the app container to read SPIFFE JWT-SVIDs (outbound)
and access the Workload API socket (inbound validation via py-spiffe).
Usage: {{- include "nv-config-manager.authVolumeMounts" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.authVolumeMounts" -}}
{{- if and .Values.spiffe.enabled (eq (.Values.spiffe.authMode | default "jwt") "jwt") }}
- name: spiffe-jwt-svid
  mountPath: /var/run/secrets/spiffe
  readOnly: true
- name: spiffe-workload-api
  mountPath: {{ .Values.spiffe.socket.mountPath | default "/spiffe-workload-api" }}
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Return "true" or "false" for the AUTH_REQUIRED env var.
Auth is required when any auth layer (JWT, OIDC, SPIFFE) is enabled.
When no auth layer is configured, services should allow unauthenticated access.
Usage: value: {{ include "nv-config-manager.authRequired" . | quote }}
*/}}
{{- define "nv-config-manager.authRequired" -}}
{{- if or .Values.gateway.auth.jwt.enabled (include "nv-config-manager.spiffe.enabled" .) .Values.oidc.enabled -}}
true
{{- else -}}
false
{{- end -}}
{{- end -}}

{{/*
Auth INI sections for nv-config-manager.ini (replaces env-var–based auth config).
Generates [auth], [auth.jwt.*], and [auth.spiffe] sections.
Usage: {{ include "nv-config-manager.authIniSections" . }}
*/}}
{{- define "nv-config-manager.authIniSections" -}}
# -----------------------------------------------------------------
# Authentication Configuration
# -----------------------------------------------------------------
[auth]
required = {{ include "nv-config-manager.authRequired" . }}
accept_request_headers = true
{{- if .Values.oidc.enabled }}
cookie_name = {{ .Values.oidc.cookieName | default "NVConfigManagerAccessToken" }}
{{- end }}
{{- if .Values.oidc.enabled }}

[auth.jwt.oidc]
issuer = {{ .Values.oidc.issuerUrl }}
audiences = {{ (concat (list (.Values.oidc.clientId | default "account")) (.Values.oidc.audiences | default list)) | join "," }}
{{- if .Values.oidc.jwksUri }}
jwks_uri = {{ .Values.oidc.jwksUri }}
{{- else if .Values.oidc.internalIssuerUrl }}
jwks_uri = {{ printf "%s/protocol/openid-connect/certs" (trimSuffix "/" .Values.oidc.internalIssuerUrl) }}
{{- end }}
{{- if .Values.oidc.groupsClaim }}
claim_groups = {{ .Values.oidc.groupsClaim }}
{{- end }}
{{- end }}
{{- range .Values.gateway.auth.jwt.providers }}

[auth.jwt.{{ .name }}]
issuer = {{ .issuer }}
audiences = {{ .audiences | join "," }}
{{- if .jwksUri }}
jwks_uri = {{ .jwksUri }}
{{- end }}
{{- if .claimMappings }}
{{- if .claimMappings.email }}
claim_email = {{ .claimMappings.email }}
{{- end }}
{{- if .claimMappings.user }}
claim_user = {{ .claimMappings.user }}
{{- end }}
{{- if .claimMappings.groups }}
claim_groups = {{ .claimMappings.groups }}
{{- end }}
{{- end }}
{{- end }}
{{- if and (index (.Values.spiffe | default dict) "enabled") (eq (index (.Values.spiffe | default dict) "authMode" | default "jwt") "jwt") }}

[auth.spiffe]
jwks_uri = {{ .Values.spiffe.jwksUri | default "/var/run/secrets/spiffe/bundle.json" }}
audiences = spiffe://{{ .Values.spiffe.trustDomain }}
jwt_svid_path = /var/run/secrets/spiffe/jwt-svid

{{- if .Values.spiffe.rbac.groupPrefixes }}
[auth.spiffe.groups]
{{- range $prefix, $group := .Values.spiffe.rbac.groupPrefixes }}
{{ $prefix }} = {{ $group }}
{{- end }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Checksum annotation for the auth INI content.
Add to pod template annotations so pods restart when auth config changes.
Usage: {{ include "nv-config-manager.authIniChecksum" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.authIniChecksum" -}}
checksum/auth-ini: {{ include "nv-config-manager.authIniSections" . | sha256sum }}
{{- end -}}

{{/*
Opt out of AWS CloudWatch Application Signals / OTel operator auto-instrumentation.
Config Manager instruments its services with the OTel SDK directly, so the
platform auto-injector must not inject its own env vars: on the Go Temporal
server the injected http/protobuf vars prevent startup, and on the Python
services they collide with the SDK's OTLP export. Apply to any pod that sets up
manual OTel export (guard with `if .Values.observability.enabled`).
Usage: {{ include "nv-config-manager.otelOptOutAnnotations" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.otelOptOutAnnotations" -}}
instrumentation.opentelemetry.io/inject-java: "false"
instrumentation.opentelemetry.io/inject-python: "false"
instrumentation.opentelemetry.io/inject-dotnet: "false"
instrumentation.opentelemetry.io/inject-nodejs: "false"
cloudwatch.aws.amazon.com/auto-annotate-java: "false"
cloudwatch.aws.amazon.com/auto-annotate-python: "false"
cloudwatch.aws.amazon.com/auto-annotate-dotnet: "false"
cloudwatch.aws.amazon.com/auto-annotate-nodejs: "false"
{{- end -}}

{{/*
Generate a JSON array of JWT provider configs for the Nautobot
nv_config_manager_auth.jwt_authentication module.  Includes:
  - OIDC provider (user_provider: true) for browser users
  - gateway.auth.jwt.providers for service-to-service issuers
Usage: {{ include "nv-config-manager.nautobot.jwtProviders" . }}
*/}}
{{- define "nv-config-manager.nautobot.jwtProviders" -}}
{{- $providers := list }}
{{- /* OIDC provider (browser users) -- creates individual Django users */ -}}
{{- if .Values.oidc.enabled }}
{{- $oidcAudiences := concat (list (.Values.oidc.clientId | default "account")) (.Values.oidc.audiences | default list) }}
{{- $oidc := dict "name" "oidc" "issuer" .Values.oidc.issuerUrl "audiences" $oidcAudiences "user_provider" true }}
{{- if .Values.oidc.jwksUri }}
{{- $oidc = merge $oidc (dict "jwks_uri" .Values.oidc.jwksUri) }}
{{- else if .Values.oidc.internalIssuerUrl }}
{{- $oidc = merge $oidc (dict "jwks_uri" (printf "%s/protocol/openid-connect/certs" (trimSuffix "/" .Values.oidc.internalIssuerUrl))) }}
{{- end }}
{{- if .Values.oidc.groupsClaim }}
{{- $oidc = merge $oidc (dict "claim_groups" .Values.oidc.groupsClaim) }}
{{- end }}
{{- $providers = append $providers $oidc }}
{{- end }}
{{- /* Service JWT providers */ -}}
{{- range .Values.gateway.auth.jwt.providers }}
{{- $p := dict "name" .name "issuer" .issuer "audiences" .audiences }}
{{- if .jwksUri }}
{{- $p = merge $p (dict "jwks_uri" .jwksUri) }}
{{- end }}
{{- if .claimMappings }}
{{- if .claimMappings.email }}
{{- $p = merge $p (dict "claim_email" .claimMappings.email) }}
{{- end }}
{{- if .claimMappings.user }}
{{- $p = merge $p (dict "claim_user" .claimMappings.user) }}
{{- end }}
{{- if .claimMappings.groups }}
{{- $p = merge $p (dict "claim_groups" .claimMappings.groups) }}
{{- end }}
{{- end }}
{{- $providers = append $providers $p }}
{{- end }}
{{- $providers | toJson }}
{{- end -}}

{{/*
=============================================================================
Wait-For Init Container Helpers
=============================================================================
Init containers that wait for dependent services to be ready before
allowing the main containers to start.
=============================================================================
*/}}

{{/*
Wait-for-Nautobot init container
Waits for Nautobot service to be available via HTTP health check.
Usage: {{ include "nv-config-manager.waitForNautobot" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.waitForNautobot" -}}
- name: wait-for-nautobot
  image: "{{ .Values.global.images.busybox.repository }}:{{ .Values.global.images.busybox.tag }}"
  imagePullPolicy: {{ .Values.global.imagePullPolicy | default "IfNotPresent" }}
  command:
    - sh
    - -c
    - |
      {{- if .Values.externalServices.nautobot.local }}
      NAUTOBOT_HOST="{{ include "nv-config-manager.componentName" (dict "root" . "component" "nautobot") }}"
      NAUTOBOT_PORT="80"
      NAUTOBOT_SCHEME="http"
      {{- else }}
      # Extract host and port from server URL
      NAUTOBOT_URL="{{ tpl .Values.externalServices.nautobot.server . }}"
      NAUTOBOT_SCHEME=$(echo "$NAUTOBOT_URL" | sed -n 's|^\(https\?\)://.*|\1|p')
      NAUTOBOT_SCHEME=${NAUTOBOT_SCHEME:-http}
      # Remove protocol prefix
      NAUTOBOT_HOST=$(echo "$NAUTOBOT_URL" | sed -e 's|^https\?://||' -e 's|/.*||' -e 's|:.*||')
      # Extract port if present, default to 443 for https, 80 for http
      if echo "$NAUTOBOT_URL" | grep -q 'https://'; then
        NAUTOBOT_PORT=$(echo "$NAUTOBOT_URL" | sed -n 's|.*:\([0-9]*\).*|\1|p')
        NAUTOBOT_PORT=${NAUTOBOT_PORT:-443}
      else
        NAUTOBOT_PORT=$(echo "$NAUTOBOT_URL" | sed -n 's|.*:\([0-9]*\).*|\1|p')
        NAUTOBOT_PORT=${NAUTOBOT_PORT:-80}
      fi
      {{- end }}
      NAUTOBOT_HEALTH_URL="${NAUTOBOT_SCHEME}://${NAUTOBOT_HOST}:${NAUTOBOT_PORT}/health"
      echo "Waiting for Nautobot at ${NAUTOBOT_HEALTH_URL}..."
      until wget -q -O- "$NAUTOBOT_HEALTH_URL" >/dev/null 2>&1; do
        echo "Nautobot not ready, waiting..."
        sleep 5
      done
      echo "Nautobot is ready!"
  securityContext:
    allowPrivilegeEscalation: false
    runAsNonRoot: true
    runAsUser: 65534
    capabilities:
      drop:
        - ALL
  resources:
    requests:
      cpu: 10m
      memory: 16Mi
    limits:
      cpu: 50m
      memory: 32Mi
{{- end -}}

{{/*
Wait-for-Redis init container
Waits for Redis service to be available via TCP check.
Usage: {{ include "nv-config-manager.waitForRedis" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.waitForRedis" -}}
- name: wait-for-redis
  image: "{{ .Values.global.images.busybox.repository }}:{{ .Values.global.images.busybox.tag }}"
  imagePullPolicy: {{ .Values.global.imagePullPolicy | default "IfNotPresent" }}
  command:
    - sh
    - -c
    - |
      {{- if .Values.externalServices.redis.local }}
      REDIS_HOST="{{ include "nv-config-manager.redisHost" . }}"
      REDIS_PORT="{{ .Values.externalServices.redis.port }}"
      {{- else }}
      REDIS_HOST="{{ .Values.externalServices.redis.host }}"
      REDIS_PORT="{{ .Values.externalServices.redis.port }}"
      {{- end }}
      echo "Waiting for Redis at ${REDIS_HOST}:${REDIS_PORT}..."
      until nc -z "$REDIS_HOST" "$REDIS_PORT"; do
        echo "Redis not ready, waiting..."
        sleep 2
      done
      echo "Redis is ready!"
  securityContext:
    allowPrivilegeEscalation: false
    runAsNonRoot: true
    runAsUser: 65534
    capabilities:
      drop:
        - ALL
  resources:
    requests:
      cpu: 10m
      memory: 16Mi
    limits:
      cpu: 50m
      memory: 32Mi
{{- end -}}

{{/*
Wait-for-NATS init container
Waits for NATS service to be available via health check endpoint.
Usage: {{ include "nv-config-manager.waitForNats" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.waitForNats" -}}
{{- $natsName := include "nv-config-manager.componentName" (dict "root" . "component" "nats") -}}
- name: wait-for-nats
  image: "{{ .Values.global.images.busybox.repository }}:{{ .Values.global.images.busybox.tag }}"
  imagePullPolicy: {{ .Values.global.imagePullPolicy | default "IfNotPresent" }}
  command:
    - sh
    - -c
    - |
      {{- if or .Values.externalServices.nats.local .Values.nautobot.enabled }}
      NATS_HOST="{{ $natsName }}"
      NATS_MONITOR_PORT="8222"
      {{- else }}
      # Extract host and port from NATS server URL (supports nats://, wss://, ws://)
      NATS_URL="{{ .Values.externalServices.nats.server }}"
      # Detect protocol for default port and health check scheme
      if echo "$NATS_URL" | grep -q '^wss://'; then
        DEFAULT_PORT="443"
        HEALTH_SCHEME="https"
      elif echo "$NATS_URL" | grep -q '^ws://'; then
        DEFAULT_PORT="80"
        HEALTH_SCHEME="http"
      else
        DEFAULT_PORT="8222"
        HEALTH_SCHEME="http"
      fi
      # Remove protocol prefix and extract host (strip path and port)
      NATS_HOST=$(echo "$NATS_URL" | sed -e 's|^[a-z]*://||' -e 's|/.*||' -e 's|:.*||')
      # Extract port if specified in URL, otherwise use default
      NATS_PORT=$(echo "$NATS_URL" | sed -e 's|^[a-z]*://||' -e 's|/.*||' | grep -o ':[0-9]*' | tr -d ':')
      NATS_MONITOR_PORT="${NATS_PORT:-$DEFAULT_PORT}"
      {{- end }}
      echo "Waiting for NATS at ${NATS_HOST}:${NATS_MONITOR_PORT}..."
      until wget -q -O- "${HEALTH_SCHEME:-http}://${NATS_HOST}:${NATS_MONITOR_PORT}/healthz" 2>/dev/null | grep -q "ok"; do
        echo "NATS not ready, waiting..."
        sleep 2
      done
      echo "NATS is ready!"
  securityContext:
    allowPrivilegeEscalation: false
    runAsNonRoot: true
    runAsUser: 65534
    capabilities:
      drop:
        - ALL
  resources:
    requests:
      cpu: 10m
      memory: 16Mi
    limits:
      cpu: 50m
      memory: 32Mi
{{- end -}}

{{/*
Wait-for-Temporal-namespace init container
Polls the Temporal frontend until the configured namespace is registered.
This must run after the nv-config-manager-worker "temporal-setup" init container has
created the namespace; use it in deployments that depend on the namespace
existing (e.g. the scheduler).
Usage: {{ include "nv-config-manager.waitForTemporalNamespace" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.waitForTemporalNamespace" -}}
{{- $temporalName := include "nv-config-manager.componentName" (dict "root" . "component" "temporal") -}}
- name: wait-for-temporal-namespace
  image: "{{ include "nv-config-manager.image" (dict "root" . "image" .Values.global.images.temporalBootstrap) }}"
  imagePullPolicy: {{ .Values.global.images.temporalBootstrap.pullPolicy }}
  command: ["/usr/local/bin/temporal-bootstrap", "wait-namespace"]
  env:
  - name: TEMPORAL_ADDR
    value: "{{ $temporalName }}-frontend-service.{{ .Values.global.namespace }}.svc.cluster.local:{{ .Values.temporal.services.frontend.port }}"
  - name: TEMPORAL_NAMESPACE
    value: {{ .Values.temporal.client.namespace | default "default" | quote }}
  resources:
    requests:
      cpu: 10m
      memory: 32Mi
    limits:
      cpu: 50m
      memory: 64Mi
  {{- include "nv-config-manager.containerSecurityContext" . | nindent 2 }}
{{- end -}}

{{/*
=============================================================================
Vault/ESO Secret Path Helpers
=============================================================================
*/}}

{{/*
Get the Vault secret path from secrets.vault.paths configuration
Usage: {{ include "nv-config-manager.vault.secretPath" (dict "root" . "secret" "nautobot") }}

Each secret must have an explicit path configured in values.
*/}}
{{- define "nv-config-manager.vault.secretPath" -}}
{{- if hasKey .root.Values.secrets.vault.paths .secret -}}
{{- $secretConfig := index .root.Values.secrets.vault.paths .secret -}}
{{- required (printf "secrets.vault.paths.%s.path is required" .secret) $secretConfig.path -}}
{{- else -}}
{{- fail (printf "Secret '%s' not found in secrets.vault.paths" .secret) -}}
{{- end -}}
{{- end -}}

{{/*
Get a key name from secrets.vault.paths configuration
Usage: {{ include "nv-config-manager.vault.keyName" (dict "root" . "secret" "nautobot" "key" "token") }}
*/}}
{{- define "nv-config-manager.vault.keyName" -}}
{{- $defaultKey := .key -}}
{{- if hasKey .root.Values.secrets.vault.paths .secret -}}
{{- $secretConfig := index .root.Values.secrets.vault.paths .secret -}}
{{- if and (hasKey $secretConfig "keys") (hasKey $secretConfig.keys .key) -}}
{{- index $secretConfig.keys .key -}}
{{- else -}}
{{- $defaultKey -}}
{{- end -}}
{{- else -}}
{{- $defaultKey -}}
{{- end -}}
{{- end -}}

{{/*
Get a configured Vault key name for optional fields. Unlike vault.keyName, this
does not fall back to the logical key name when the field is absent or empty.
*/}}
{{- define "nv-config-manager.vault.configuredKeyName" -}}
{{- if hasKey .root.Values.secrets.vault.paths .secret -}}
{{- $secretConfig := index .root.Values.secrets.vault.paths .secret -}}
{{- if and (hasKey $secretConfig "keys") (hasKey $secretConfig.keys .key) (index $secretConfig.keys .key) -}}
{{- index $secretConfig.keys .key -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/*
=============================================================================
Template Plugins Init Container Helper
=============================================================================
Installs template plugins from a PVC or plugin source images into a target
directory for discovery by the render service via Python entry points.
=============================================================================
*/}}

{{/*
Copy template plugin source images into the shared staging directory.
Usage: {{ include "nv-config-manager.copyTemplatePluginImages" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.copyTemplatePluginImages" -}}
{{- if and .Values.renderService.templatePlugins.enabled .Values.renderService.templatePlugins.images }}
{{- $mountPath := .Values.renderService.templatePlugins.mountPath -}}
{{- range .Values.renderService.templatePlugins.images }}
{{- $name := .name | lower | replace "_" "-" | trunc 45 | trimSuffix "-" }}
- name: copy-template-plugin-{{ $name }}
  image: {{ required "renderService.templatePlugins.images[].image is required" .image }}
  imagePullPolicy: {{ .pullPolicy | default "IfNotPresent" }}
  command: ["/bin/sh", "-c"]
  args:
  - |
    set -e
    target="{{ $mountPath }}/{{ $name }}"
    mkdir -p "$target"
    if [ -d /plugin-wheels ]; then
      mkdir -p "$target/wheels"
      cp -R /plugin-wheels/. "$target/wheels/"
    fi
    if [ -d /plugin-source ]; then
      mkdir -p "$target/source"
      cp -R /plugin-source/. "$target/source/"
    fi
  volumeMounts:
  - name: template-plugins-source
    mountPath: {{ $mountPath }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Install template plugins init container
Uses a Python image with pip to install plugin wheels or source.
Usage: {{ include "nv-config-manager.installTemplatePlugins" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.installTemplatePlugins" -}}
{{- if .Values.renderService.templatePlugins.enabled }}
- name: install-template-plugins
  image: {{ .Values.renderService.templatePlugins.installerImage | default "python:3.13-alpine" }}
  imagePullPolicy: IfNotPresent
  command: ["/bin/sh", "-c"]
  args:
  - |
    set -e
    echo "Installing build dependencies..."
    pip install --quiet hatchling
    echo "Installing template plugins from {{ .Values.renderService.templatePlugins.mountPath }}..."
    for plugin_dir in {{ .Values.renderService.templatePlugins.mountPath }}/*; do
      if [ ! -d "$plugin_dir" ]; then
        continue
      fi
      if ls "$plugin_dir"/wheels/*.whl >/dev/null 2>&1; then
        echo "Installing plugin wheel(s): $plugin_dir/wheels"
        pip install --target=/opt/plugins --no-deps "$plugin_dir"/wheels/*.whl
      elif [ -f "$plugin_dir/source/pyproject.toml" ]; then
        echo "Installing plugin source: $plugin_dir/source"
        pip install --target=/opt/plugins --no-deps "$plugin_dir/source"
      elif [ -f "$plugin_dir/pyproject.toml" ]; then
        echo "Installing plugin source: $plugin_dir"
        pip install --target=/opt/plugins --no-deps "$plugin_dir"
      fi
    done
    echo "Template plugins installation complete"
    ls -la /opt/plugins/ 2>/dev/null || echo "No plugins installed"
  volumeMounts:
  - name: template-plugins-source
    mountPath: {{ .Values.renderService.templatePlugins.mountPath }}
    readOnly: true
  - name: template-plugins-installed
    mountPath: /opt/plugins
{{- end }}
{{- end -}}

{{/*
Template plugin source and install volumes.
Usage: {{ include "nv-config-manager.templatePluginVolumes" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.templatePluginVolumes" -}}
{{- if .Values.renderService.templatePlugins.enabled }}
- name: template-plugins-source
  {{- if .Values.renderService.templatePlugins.images }}
  emptyDir: {}
  {{- else }}
  persistentVolumeClaim:
    claimName: {{ .Values.renderService.templatePlugins.pvcName }}
  {{- end }}
- name: template-plugins-installed
  emptyDir: {}
{{- end }}
{{- end -}}

{{/*
=============================================================================
DCIM Provider Package Helpers
=============================================================================
External DCIM providers are installed into an isolated shared volume. The
provider image is the complete offline artifact: all provider and dependency
wheels must be present under /plugin-wheels.
=============================================================================
*/}}

{{- define "nv-config-manager.copyDCIMProviderImages" -}}
{{- if .Values.dcim.providerPackages.enabled -}}
{{- if not .Values.dcim.providerPackages.images -}}
{{- fail "dcim.providerPackages.images is required when dcim.providerPackages.enabled is true" -}}
{{- end -}}
{{- range .Values.dcim.providerPackages.images }}
{{- $name := required "dcim.providerPackages.images[].name is required" .name | lower | replace "_" "-" | trunc 45 | trimSuffix "-" }}
- name: copy-dcim-provider-{{ $name }}
  image: {{ required "dcim.providerPackages.images[].image is required" .image }}
  imagePullPolicy: {{ .pullPolicy | default "IfNotPresent" }}
  command: ["/bin/sh", "-c"]
  args:
  - |
    set -e
    if ! ls /plugin-wheels/*.whl >/dev/null 2>&1; then
      echo "DCIM provider image does not contain /plugin-wheels/*.whl" >&2
      exit 1
    fi
    target="/opt/dcim-provider-packages/{{ $name }}/wheels"
    mkdir -p "$target"
    cp -R /plugin-wheels/. "$target/"
  volumeMounts:
  - name: dcim-provider-packages-source
    mountPath: /opt/dcim-provider-packages
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.installDCIMProviderPackages" -}}
{{- if .Values.dcim.providerPackages.enabled }}
- name: install-dcim-provider-packages
  image: {{ .Values.dcim.providerPackages.installerImage | default "python:3.13-bookworm" }}
  imagePullPolicy: IfNotPresent
  command: ["/bin/sh", "-c"]
  args:
  - |
    set -e
    for package_dir in /opt/dcim-provider-packages/*; do
      if [ ! -d "$package_dir/wheels" ]; then
        continue
      fi
      echo "Installing offline DCIM provider wheels from $package_dir/wheels"
      pip install --no-index --find-links="$package_dir/wheels" --target=/opt/dcim-providers "$package_dir"/wheels/*.whl
    done
    test -d /opt/dcim-providers
  volumeMounts:
  - name: dcim-provider-packages-source
    mountPath: /opt/dcim-provider-packages
    readOnly: true
  - name: dcim-provider-packages-installed
    mountPath: /opt/dcim-providers
{{- end }}
{{- end -}}

{{- define "nv-config-manager.dcimProviderPackageVolumes" -}}
{{- if .Values.dcim.providerPackages.enabled }}
- name: dcim-provider-packages-source
  emptyDir: {}
- name: dcim-provider-packages-installed
  emptyDir: {}
{{- end }}
{{- end -}}

{{- define "nv-config-manager.dcimProviderPackageVolumeMount" -}}
{{- if .Values.dcim.providerPackages.enabled }}
- name: dcim-provider-packages-installed
  mountPath: /opt/dcim-providers
  readOnly: true
{{- end }}
{{- end -}}

{{- define "nv-config-manager.dcimProviderPythonPath" -}}
{{- if .Values.dcim.providerPackages.enabled }}
- name: PYTHONPATH
  value: "/opt/dcim-providers:${PYTHONPATH:-}"
{{- end }}
{{- end -}}

{{- define "nv-config-manager.pythonPluginPath" -}}
{{- $paths := list -}}
{{- if .Values.dcim.providerPackages.enabled -}}
{{- $paths = append $paths "/opt/dcim-providers" -}}
{{- end -}}
{{- if .Values.renderService.templatePlugins.enabled -}}
{{- $paths = append $paths "/opt/plugins" -}}
{{- end -}}
{{- if $paths }}
- name: PYTHONPATH
  value: "{{ join ":" $paths }}:${PYTHONPATH:-}"
{{- end -}}
{{- end -}}

{{/*
Create a default fully qualified app name.
We truncate at 63 chars because some Kubernetes name fields are limited to this (by the DNS naming spec).
If release name contains chart name it will be used as a full name.
*/}}
{{- define "nv-config-manager.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Generate a component resource name using the fullname pattern.
Usage: {{ include "nv-config-manager.componentName" (dict "root" . "component" "ui") }}

Result depends on configuration:
- Default: <release>-<chart>-<component> (e.g., "my-release-nv-config-manager-ui")
- With fullnameOverride: <override>-<component> (e.g., "custom-ui")
- With nameOverride: <release>-<nameOverride>-<component>

*/}}
{{- define "nv-config-manager.componentName" -}}
{{- $fullname := include "nv-config-manager.fullname" .root -}}
{{- printf "%s-%s" $fullname .component | trunc 63 | trimSuffix "-" -}}
{{- end }}

{{/*
Generate a chart-owned CNPG PodMonitor name.

Short names remain readable. If the release + cluster name exceeds the DNS
label limit, reserve space for a hash of the complete identity so truncation
cannot collapse monitors for different releases or clusters onto one name.
*/}}
{{- define "nv-config-manager.cnpgPodMonitorName" -}}
{{- $fullname := include "nv-config-manager.fullname" .root -}}
{{- $name := printf "%s-cnpg-%s" $fullname .clusterName -}}
{{- if gt (len $name) 63 -}}
{{- $hash := sha256sum (printf "%s/%s" $fullname .clusterName) | trunc 10 -}}
{{- printf "%s-%s" ($name | trunc 52 | trimSuffix "-") $hash -}}
{{- else -}}
{{- $name -}}
{{- end -}}
{{- end }}

{{/*
Address of the Prometheus this release deploys, for KEDA's render-autoscaling
trigger -- empty unless that in-cluster Prometheus is actually what KEDA will
query.

Returns nothing when renderService.autoscaling.prometheus.serverAddress is set
(KEDA queries that endpoint instead) or when the prometheus subchart is off.
Both the ScaledObject's serverAddress and the NetworkPolicy rule that lets KEDA
reach it derive from this, so the policy cannot grant cross-namespace access to
a Prometheus KEDA never talks to.

The FQDN is required because KEDA resolves it from its own namespace, not the
release's.

The service name comes from the subchart's own helper rather than being rebuilt
here, so every input it honours -- server.fullnameOverride, nameOverride,
server.name, and its rule that drops the chart-name segment when the release
name already contains it -- moves the trigger and the NetworkPolicy with it.
Rebuilding the name locally silently missed all but the first: a wrong name is
still a non-empty address, so the required guard passes, KEDA accepts the
ScaledObject, and it just never scales.

Subchart helpers are registered globally even while the subchart is disabled,
but expect subchart scope, hence the synthesised dict. Chart.Name must be
"prometheus" because that is what upstream falls back to for nameOverride.
*/}}
{{- define "nv-config-manager.inClusterPrometheusAddress" -}}
{{- if and .Values.prometheus.enabled (not .Values.renderService.autoscaling.prometheus.serverAddress) -}}
{{- $name := include "prometheus.server.fullname" (dict "Values" .Values.prometheus "Chart" (dict "Name" "prometheus") "Release" .Release) -}}
{{- printf "http://%s.%s.svc.cluster.local:9090" $name .Release.Namespace -}}
{{- end -}}
{{- end }}

{{/*
Common secret names
*/}}
{{- define "nv-config-manager.iniSecretName" -}}
{{- include "nv-config-manager.componentName" (dict "root" . "component" "ini") -}}
{{- end }}

{{- define "nv-config-manager.mcpAuthConfigMapName" -}}
{{- include "nv-config-manager.componentName" (dict "root" . "component" "mcp-auth") -}}
{{- end }}

{{/*
Public MCP OAuth metadata ConfigMap data.
*/}}
{{- define "nv-config-manager.configmap.mcp-auth" -}}
{{- $oauth := dig "auth" "oauth" (dict) .Values.mcp -}}
{{- $enabled := .Values.oidc.enabled -}}
{{- if hasKey $oauth "enabled" -}}
{{- $enabled = $oauth.enabled -}}
{{- end -}}
mcp-auth.ini: |
  [mcp.oauth]
  enabled = {{ $enabled }}
  {{- if $enabled }}
  {{- $resourceUrl := get $oauth "resourceUrl" | default (printf "https://%s/mcp" (tpl (.Values.mcp.gateway.svcHostname | default "") .)) -}}
  {{- $resourceUrl = trimSuffix "/" (tpl $resourceUrl .) -}}
  {{- $forwardResourceParameter := true -}}
  {{- if hasKey $oauth "forwardResourceParameter" -}}
  {{- $forwardResourceParameter = $oauth.forwardResourceParameter -}}
  {{- end -}}
  {{- $issuerUrl := get $oauth "issuerUrl" | default .Values.oidc.issuerUrl -}}
  {{- $issuerUrl = required "mcp.auth.oauth.issuerUrl or oidc.issuerUrl is required when MCP OAuth metadata is enabled" $issuerUrl -}}
  {{- $issuerUrl = trimSuffix "/" (tpl $issuerUrl .) -}}
  {{- $clientId := get $oauth "clientId" | default .Values.oidc.cliClientId | default .Values.oidc.clientId -}}
  {{- $clientId = required "mcp.auth.oauth.clientId or oidc.cliClientId/clientId is required when MCP OAuth metadata is enabled" $clientId -}}
  {{- $authorizationEndpoint := get $oauth "authorizationEndpoint" | default .Values.oidc.authorizationEndpoint -}}
  {{- if $authorizationEndpoint -}}
  {{- $authorizationEndpoint = trimSuffix "/" (tpl $authorizationEndpoint .) -}}
  {{- else -}}
  {{- $authorizationEndpoint = printf "%s/protocol/openid-connect/auth" $issuerUrl -}}
  {{- end -}}
  {{- $tokenEndpoint := get $oauth "tokenEndpoint" | default .Values.oidc.tokenEndpoint -}}
  {{- if $tokenEndpoint -}}
  {{- $tokenEndpoint = trimSuffix "/" (tpl $tokenEndpoint .) -}}
  {{- else -}}
  {{- $tokenEndpoint = printf "%s/protocol/openid-connect/token" $issuerUrl -}}
  {{- end -}}
  {{- $jwksUri := get $oauth "jwksUri" | default .Values.oidc.jwksUri -}}
  {{- if $jwksUri -}}
  {{- $jwksUri = trimSuffix "/" (tpl $jwksUri .) -}}
  {{- end -}}
  {{- $scopes := get $oauth "scopes" | default .Values.oidc.scopes -}}
  {{- if not $scopes -}}
  {{- $scopes = list "openid" "email" "profile" -}}
  {{- end }}
  resource_url = {{ $resourceUrl }}
  issuer_url = {{ $issuerUrl }}
  client_id = {{ $clientId }}
  scopes = {{ if kindIs "string" $scopes }}{{ $scopes }}{{ else }}{{ join " " $scopes }}{{ end }}
  authorization_endpoint = {{ $authorizationEndpoint }}
  token_endpoint = {{ $tokenEndpoint }}
  forward_resource_parameter = {{ $forwardResourceParameter }}
  {{- if $jwksUri }}
  jwks_uri = {{ $jwksUri }}
  {{- end }}
  {{- end }}
{{- end }}

{{- define "nv-config-manager.networkSecretsName" -}}
{{- include "nv-config-manager.componentName" (dict "root" . "component" "network-secrets") -}}
{{- end }}

{{- define "nv-config-manager.natsUser" -}}
{{- .Values.externalServices.nats.user | default "nv-config-manager" -}}
{{- end }}

{{- define "nv-config-manager.natsSecretName" -}}
{{- .Values.externalServices.nats.secretName | default (printf "nats-%s" (include "nv-config-manager.natsUser" .)) -}}
{{- end }}

{{- define "nv-config-manager.natsExternalSecretName" -}}
{{- .Values.externalServices.nats.externalSecretName | default (printf "%s-eso" (include "nv-config-manager.natsSecretName" .)) -}}
{{- end }}

{{- define "nv-config-manager.configManagerConsumerName" -}}
{{- required "externalServices.nats.streams.configManager.consumer.name is required" .Values.externalServices.nats.streams.configManager.consumer.name -}}
{{- end -}}

{{- define "nv-config-manager.archiveConsumerName" -}}
{{- required "externalServices.nats.streams.configManager.archiveConsumer.name is required" .Values.externalServices.nats.streams.configManager.archiveConsumer.name -}}
{{- end -}}

{{- define "nv-config-manager.archiveConsumerDeliverySubject" -}}
{{- required "externalServices.nats.streams.configManager.archiveConsumer.deliverySubject is required" .Values.externalServices.nats.streams.configManager.archiveConsumer.deliverySubject -}}
{{- end -}}

{{- define "nv-config-manager.nautobotConsumerName" -}}
{{- required "externalServices.nats.streams.nautobot.consumer.name is required" .Values.externalServices.nats.streams.nautobot.consumer.name -}}
{{- end -}}

{{- define "nv-config-manager.vaultSecretStoreName" -}}
{{- .Values.secrets.vault.secretStoreName | default "vault-secretstore-nv-config-manager" -}}
{{- end }}

{{- define "nv-config-manager.vaultNetworkSecretStoreName" -}}
{{- .Values.secrets.vault.networkSecretStoreName | default "vault-secretstore-nv-config-manager-network" -}}
{{- end }}

{{- define "nv-config-manager.temporalWorkerName" -}}
{{- if .Values.temporal.configManagerWorker.nameOverride -}}
{{- .Values.temporal.configManagerWorker.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $temporalName := include "nv-config-manager.componentName" (dict "root" . "component" "temporal") -}}
{{- $suffix := .Values.temporal.configManagerWorker.nameSuffix | default "nv-config-manager-worker" -}}
{{- printf "%s-%s" $temporalName $suffix | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{/*
Self-signed CA certificate environment variables for Python/Node.js
Usage: {{ include "nv-config-manager.selfSignedCA.env" . }}
*/}}
{{- define "nv-config-manager.selfSignedCA.env" -}}
{{- if .Values.gateway.certificates.selfSigned }}
# Trust self-signed CA certificate
- name: REQUESTS_CA_BUNDLE
  value: /etc/ssl/certs/nv-config-manager-ca.crt
- name: SSL_CERT_FILE
  value: /etc/ssl/certs/nv-config-manager-ca.crt
- name: CURL_CA_BUNDLE
  value: /etc/ssl/certs/nv-config-manager-ca.crt
- name: NODE_EXTRA_CA_CERTS
  value: /etc/ssl/certs/nv-config-manager-ca.crt
{{- end }}
{{- end -}}

{{/*
Self-signed CA certificate volume mount
Usage: {{ include "nv-config-manager.selfSignedCA.volumeMount" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.selfSignedCA.volumeMount" -}}
{{- if .Values.gateway.certificates.selfSigned }}
- name: ca-cert
  mountPath: /etc/ssl/certs/nv-config-manager-ca.crt
  subPath: ca.crt
  readOnly: true
{{- end }}
{{- end -}}

{{/*
Self-signed CA certificate volume definition
Usage: {{ include "nv-config-manager.selfSignedCA.volume" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.selfSignedCA.volume" -}}
{{- if .Values.gateway.certificates.selfSigned }}
{{- $tlsCertName := include "nv-config-manager.componentName" (dict "root" . "component" "gateway-tls") }}
- name: ca-cert
  secret:
    secretName: {{ $tlsCertName }}
    items:
    - key: ca.crt
      path: ca.crt
{{- end }}
{{- end -}}

{{/*
=============================================================================
Pod Scheduling Helpers (nodeSelector, affinity, topologySpreadConstraints)
=============================================================================
These helpers support multi-node deployments by allowing workloads to be
distributed across nodes based on labels and constraints.

Node labels expected (when using --size large):
  - nv-config-manager.nvidia.com/node-type=control-plane (Temporal, Gateway, etc.)
  - nv-config-manager.nvidia.com/node-type=worker (workers, consumers)
  - nv-config-manager.nvidia.com/node-type=database (CNPG clusters)
=============================================================================
*/}}

{{/*
Node selector helper
Renders nodeSelector if defined in the service configuration.
To disable nodeSelector, set it to `null` in your values override:
  networkZtp:
    nodeSelector: null
Usage: {{ include "nv-config-manager.nodeSelector" .Values.renderService.api | nindent 6 }}
*/}}
{{- define "nv-config-manager.nodeSelector" -}}
{{- if .nodeSelector }}
nodeSelector:
  {{- toYaml .nodeSelector | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Affinity helper
Renders affinity (nodeAffinity, podAffinity, podAntiAffinity) if defined.
Usage: {{ include "nv-config-manager.affinity" .Values.renderService.api | nindent 6 }}
*/}}
{{- define "nv-config-manager.affinity" -}}
{{- if .affinity }}
affinity:
  {{- toYaml .affinity | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Topology spread constraints helper
Renders topologySpreadConstraints if defined for even distribution across nodes.
Usage: {{ include "nv-config-manager.topologySpreadConstraints" .Values.renderService.api | nindent 6 }}
*/}}
{{- define "nv-config-manager.topologySpreadConstraints" -}}
{{- if .topologySpreadConstraints }}
topologySpreadConstraints:
  {{- toYaml .topologySpreadConstraints | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Combined scheduling helper (nodeSelector + affinity + topologySpreadConstraints)
Renders all scheduling constraints in one call.
Usage: {{ include "nv-config-manager.scheduling" .Values.renderService.api | nindent 6 }}
*/}}
{{- define "nv-config-manager.scheduling" -}}
{{- include "nv-config-manager.nodeSelector" . }}
{{- include "nv-config-manager.affinity" . }}
{{- include "nv-config-manager.topologySpreadConstraints" . }}
{{- end -}}

{{/*
Custom labels env var -- emits a NV_CONFIG_MANAGER_CUSTOM_LABELS env entry (JSON string)
when global.customLabels is non-empty.
Usage: {{ include "nv-config-manager.customLabelsEnv" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.customLabelsEnv" -}}
{{- if .Values.global.customLabels }}
- name: NV_CONFIG_MANAGER_CUSTOM_LABELS
  value: {{ .Values.global.customLabels | toJson | quote }}
{{- end }}
{{- end -}}

{{/*
FastAPI metrics env var -- controls whether response status labels are grouped
into classes such as 2xx and 5xx.
Usage: {{ include "nv-config-manager.fastApiMetricsEnv" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.fastApiMetricsEnv" -}}
- name: NV_CONFIG_MANAGER_GROUP_FASTAPI_STATUS_CODES
  value: {{ .Values.monitoring.groupFastApiStatusCodes | quote }}
{{- end -}}

{{/*
Network ZTP storage env vars. Only Ceph uses env refs because Rook generates
the endpoint and credentials Secret at runtime. Other storage settings render
into the main INI.
Usage: {{ include "nv-config-manager.networkZtpStorageEnv" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.networkZtp.s3CephUserSecretName" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- $user := $ceph.objectStoreUser | default dict -}}
{{- $storeName := $user.store | default "ceph-objectstore" -}}
{{- $userName := $user.name | default "ztp-user" -}}
{{- $ceph.userSecretName | default (printf "rook-ceph-object-user-%s-%s" $storeName $userName) -}}
{{- end -}}

{{- define "nv-config-manager.networkZtp.s3EsoIniEnabled" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- $ztpS3 := index (.Values.secrets.vault.paths | default dict) "ztpS3" | default dict -}}
{{- if and .Values.networkZtp.enabled (eq .Values.networkZtp.storage.type "s3") (eq .Values.secrets.method "eso") (not ($ceph.enabled | default false)) (not $s3.credentialsSecret) ($ztpS3.path | default "") -}}
true
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.networkZtp.s3VaultAgentIniEnabled" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- $ztpS3 := index (.Values.secrets.vault.paths | default dict) "ztpS3" | default dict -}}
{{- if and .Values.networkZtp.enabled (eq .Values.networkZtp.storage.type "s3") (eq .Values.secrets.method "vault-agent") (not ($ceph.enabled | default false)) (not $s3.credentialsSecret) ($ztpS3.path | default "") -}}
true
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.networkZtp.s3ExistingSecretIniEnabled" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- if and .Values.networkZtp.enabled (eq .Values.networkZtp.storage.type "s3") (eq .Values.secrets.method "kubernetes") (not ($ceph.enabled | default false)) $s3.credentialsSecret -}}
true
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.networkZtp.s3CredentialSource" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- if $ceph.enabled | default false -}}
ceph
{{- else if eq (include "nv-config-manager.networkZtp.s3VaultAgentIniEnabled" .) "true" -}}
vault-agent
{{- else if $s3.credentialsSecret -}}
existing-secret
{{- else if eq (include "nv-config-manager.networkZtp.s3EsoIniEnabled" .) "true" -}}
eso
{{- else -}}
default
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.networkZtpIniStorageConfig" -}}
{{- $storage := .Values.networkZtp.storage | default dict -}}
{{- $storageType := $storage.type | default "s3" -}}
storage_type = {{ $storageType }}
{{ if eq $storageType "file" -}}
file_store_path = {{ $storage.file.mountPath | default "/mnt/images" }}
{{ else if eq $storageType "s3" -}}
{{- $s3 := $storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
s3_bucket = {{ $s3.bucketName | default "ngc-network-firmware-images" }}
{{ if and (not ($ceph.enabled | default false)) $s3.endpoint -}}
s3_endpoint = {{ $s3.endpoint }}
{{ end -}}
{{ if and (not ($ceph.enabled | default false)) $s3.region -}}
s3_region = {{ $s3.region }}
{{ end -}}
{{ end -}}
{{- end -}}

{{/*
Network ZTP download settings rendered into the main INI.
*/}}
{{- define "nv-config-manager.networkZtpIniDownloadConfig" -}}
{{- $downloads := .Values.networkZtp.downloads | default dict -}}
{{- $http := $downloads.http | default dict -}}
{{- $sftp := $downloads.sftp | default dict -}}
http_stream_chunk_bytes = {{ $http.chunkSizeBytes | default 67108864 | int }}
http_max_concurrent_downloads = {{ $http.maxConcurrentDownloads | default 16 | int }}
sftp_read_ahead_bytes = {{ $sftp.readAheadBytes | default 16777216 | int }}
sftp_max_concurrent_downloads = {{ $sftp.maxConcurrentDownloads | default 32 | int }}
sftp_metrics_port = {{ $sftp.metricsPort | default 9100 | int }}
{{- end -}}

{{- define "nv-config-manager.networkZtpExistingSecretIniConfig" -}}
{{- if eq (include "nv-config-manager.networkZtp.s3ExistingSecretIniEnabled" .) "true" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $mountPath := "/secrets/ztp-s3-credentials" -}}
{{- $endpointSecretKey := $s3.endpointSecretKey | default "CUSTOM_S3_ENDPOINT" -}}
{{- if and (not $s3.endpoint) $endpointSecretKey }}
s3_endpoint = $(cat {{ $mountPath }}/{{ $endpointSecretKey }})
{{- end }}
s3_access_key = $(cat {{ $mountPath }}/{{ $s3.accessKeySecretKey | default "CUSTOM_S3_ACCESS_KEY" }})
s3_secret_key = $(cat {{ $mountPath }}/{{ $s3.secretKeySecretKey | default "CUSTOM_S3_SECRET_KEY" }})
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.networkZtpStorageEnv" -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict }}
{{- $ceph := $s3.ceph | default dict }}
{{- $cephSecret := include "nv-config-manager.networkZtp.s3CephUserSecretName" . }}
{{- $credentialSource := include "nv-config-manager.networkZtp.s3CredentialSource" . }}
{{- if and (eq .Values.networkZtp.storage.type "s3") (eq $credentialSource "ceph") }}
- name: CUSTOM_S3_ENDPOINT
  valueFrom:
    secretKeyRef:
      name: {{ $cephSecret | quote }}
      key: {{ $ceph.endpointSecretKey | default "Endpoint" | quote }}
- name: CUSTOM_S3_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $cephSecret | quote }}
      key: {{ $ceph.accessKeySecretKey | default "AccessKey" | quote }}
- name: CUSTOM_S3_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ $cephSecret | quote }}
      key: {{ $ceph.secretKeySecretKey | default "SecretKey" | quote }}
{{- end }}
{{- end -}}

{{- define "nv-config-manager.networkZtpEntrypoint" -}}
{{- $command := required "command is required" .command -}}
{{- $args := .args | default (list) -}}
command: {{ $command | toJson }}
{{- if hasKey . "args" }}
args: {{ $args | toJson }}
{{- end }}
{{- end -}}

{{/*
Custom pod labels -- emits global.customLabels as pod labels for
PodMonitor podTargetLabels to promote onto Prometheus metrics.
Usage: {{ include "nv-config-manager.customPodLabels" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.customPodLabels" -}}
{{- with .Values.global.customLabels }}
{{- toYaml . }}
{{- end }}
{{- end -}}

{{/*
PodMonitor podTargetLabels -- lists global.customLabels keys so Prometheus
copies them from the pod onto every scraped metric.
Usage: {{ include "nv-config-manager.podTargetLabels" . | nindent 2 }}
*/}}
{{- define "nv-config-manager.podTargetLabels" -}}
{{- if .Values.global.customLabels }}
podTargetLabels:
  {{- range $key, $val := .Values.global.customLabels }}
  - {{ $key }}
  {{- end }}
{{- end }}
{{- end -}}

{{/*
PodMonitor podMetricsEndpoints metricRelabelings -- copies global.customLabels onto scraped samples
using sourceLabels job (same pattern as nv-config-manager-network-dhcp Helm chart).
Outputs list items only (no metricRelabelings: key).
Usage under each endpoint:
      {{- if .Values.global.customLabels }}
      metricRelabelings:
        {{- include "nv-config-manager.podMonitorMetricRelabelings" . | nindent 8 }}
      {{- end }}
*/}}
{{- define "nv-config-manager.podMonitorMetricRelabelings" }}
{{- range $k, $v := (.Values.global.customLabels | default dict) -}}
- action: replace
  sourceLabels: [job]
  regex: .*
  targetLabel: {{ $k }}
  replacement: {{ $v | toString | quote }}
{{ end }}
{{- end }}

{{/*
Probe staticConfig labels -- emits global.customLabels under
spec.targets.staticConfig.labels so Prometheus attaches them to every
blackbox sample scraped from the Probe's static targets. The Probe CR's
metadata.labels are NOT propagated onto samples by Prometheus Operator, so
this helper is the only way to surface customLabels (e.g. `include_in_slo`,
`production`) on probe metrics. Yields nothing when global.customLabels is
empty/missing.

Usage (the `labels:` key lives at 6 spaces under the Probe spec, hence
`nindent 6`):
  targets:
    staticConfig:
      {{- include "nv-config-manager.probeStaticConfigLabels" . | nindent 6 }}
      static:
      - {{ ... }}
*/}}
{{- define "nv-config-manager.probeStaticConfigLabels" -}}
{{- with .Values.global.customLabels -}}
labels:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
=============================================================================
Security Context Helpers
=============================================================================
Standard pod and container security contexts for NVIDIA Config Manager workloads.
Apply to all Deployments/Jobs that don't have specific overrides.
=============================================================================
*/}}

{{/*
Pod-level securityContext — applies seccomp profile.
Usage: {{- include "nv-config-manager.podSecurityContext" . | nindent 6 }}
*/}}
{{- define "nv-config-manager.podSecurityContext" -}}
securityContext:
  fsGroup: 1000
  seccompProfile:
    type: RuntimeDefault
{{- end -}}

{{/*
Container-level securityContext for distroless (nonroot) containers.
Drops all capabilities, prevents privilege escalation.
Usage: {{- include "nv-config-manager.containerSecurityContext" . | nindent 8 }}
*/}}
{{- define "nv-config-manager.containerSecurityContext" -}}
securityContext:
  allowPrivilegeEscalation: false
  runAsNonRoot: true
  runAsUser: 1000
  runAsGroup: 1000
  capabilities:
    drop:
      - ALL
{{- end -}}

{{/*
Validate secrets configuration.
Call once at the top of any template that branches on secrets.method.
Fails with a clear message when method is missing or unrecognised,
which is the symptom of a duplicate top-level `secrets:` key in the
generated values file.
*/}}
{{/*
Resolve the external-dns hostname for a service's ingress configuration.
Checks nlb.dns_name, cilium.hostname, and metallb.hostname in order.
Usage: {{ include "nv-config-manager.externalDnsHostname" .Values.networkZtp.ingress }}
*/}}
{{- define "nv-config-manager.externalDnsHostname" -}}
{{- $hostname := "" -}}
{{- if and .nlb .nlb.dns_name -}}
  {{- $hostname = .nlb.dns_name -}}
{{- else if and .cilium .cilium.hostname -}}
  {{- $hostname = .cilium.hostname -}}
{{- else if and .metallb .metallb.hostname -}}
  {{- $hostname = .metallb.hostname -}}
{{- end -}}
{{- if $hostname }}
    external-dns.alpha.kubernetes.io/hostname: {{ $hostname }}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.validateSecrets" -}}
{{- if not .Values.secrets.method -}}
  {{- fail "secrets.method is not set. This usually means values-generated.yaml contains a duplicate top-level 'secrets:' key — check installer-generated values output." -}}
{{- end -}}
{{- if not (or (eq .Values.secrets.method "eso") (eq .Values.secrets.method "kubernetes") (eq .Values.secrets.method "vault-agent")) -}}
  {{- fail (printf "secrets.method must be 'eso', 'kubernetes', or 'vault-agent', got '%s'" .Values.secrets.method) -}}
{{- end -}}
{{- if and (eq .Values.secrets.method "vault-agent") .Values.secrets.vault.tokenAuth.enabled -}}
  {{- fail "secrets.method=vault-agent requires Kubernetes auth to Vault (set secrets.vault.tokenAuth.enabled=false)" -}}
{{- end -}}
{{- if and (eq .Values.secrets.method "vault-agent") .Values.customConfig.enabled .Values.customConfig.vaultSecrets -}}
  {{- fail "customConfig.vaultSecrets is not supported when secrets.method=vault-agent (remove vaultSecrets or use secrets.method=eso)" -}}
{{- end -}}
{{- if and (eq .Values.secrets.method "vault-agent") (eq (.Values.secrets.vaultAgent.autoAuthMethod | default "kubernetes") "jwt") (not .Values.secrets.vaultAgent.serviceAccountTokenAudience) -}}
  {{- fail "secrets.vaultAgent.serviceAccountTokenAudience is required when secrets.vaultAgent.autoAuthMethod=jwt (projected SA token / ESO kubernetesServiceAccountToken audiences)" -}}
{{- end -}}
{{- $s3 := .Values.networkZtp.storage.s3 | default dict -}}
{{- $ceph := $s3.ceph | default dict -}}
{{- if and .Values.networkZtp.enabled (eq .Values.networkZtp.storage.type "s3") $s3.credentialsSecret (not ($ceph.enabled | default false)) (ne .Values.secrets.method "kubernetes") -}}
  {{- fail "networkZtp.storage.s3.credentialsSecret now uses the Kubernetes secret-assembler and requires secrets.method=kubernetes; use secrets.vault.paths.ztpS3 with secrets.method=eso or vault-agent" -}}
{{- end -}}
{{- end -}}

{{/*
OTel app-container env shared by every instrumented service (the HTTP APIs
config-store/dhcp/ztp/render/mcp and the Temporal worker/api/scheduler/archive).
Call sites guard the include with `if .Values.observability.enabled`.

Points the OTel SDK at an existing OTLP collector:
  - observability.otlpEndpoint when set (managed or external collector), else
  - the in-cluster Grafana Alloy service, but only when the bundled Alloy is
    enabled (alloy.enabled, e.g. via values-observability.yaml).
Fails fast when neither is available so pods never point at a dead OTLP target.

  Context: (dict "root" $ "serviceName" "<service.name>").
*/}}
{{- define "nv-config-manager.otelAppEnv" -}}
{{- $endpoint := .root.Values.observability.otlpEndpoint -}}
{{- $alloy := .root.Values.alloy | default dict -}}
{{- if and (not $endpoint) ($alloy.enabled | default false) -}}
{{- $endpoint = printf "http://alloy.%s.svc.cluster.local:4317" .root.Values.global.namespace -}}
{{- end -}}
{{- if not $endpoint -}}
{{- fail "observability.enabled=true requires observability.otlpEndpoint to be set, or the bundled Alloy collector enabled (alloy.enabled=true, e.g. values-observability.yaml)" -}}
{{- end -}}
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ $endpoint | quote }}
- name: OTEL_SERVICE_NAME
  value: {{ .serviceName | quote }}
{{- end -}}
