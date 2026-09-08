{{/*
=============================================================================
Vault Agent (secrets.method=vault-agent)
=============================================================================
Uses the Vault Agent Injector with a ConfigMap-backed config.hcl. File-based
secrets match External Secrets Operator paths from secrets.vault.paths.
=============================================================================
*/}}

{{- define "nv-config-manager.vault.kvMountPath" -}}
{{- .Values.secrets.vault.secretsPath | default (printf "%s/secrets" .Values.secrets.vault.namespace) -}}
{{- end -}}

{{- define "nv-config-manager.vault.configKvMountPath" -}}
{{- .Values.secrets.vault.configSecretsPath | default .Values.secrets.vault.secretsPath | default (printf "%s/secrets" .Values.secrets.vault.namespace) -}}
{{- end -}}

{{/*
Projected SA token volume name (JWT auto-auth). Injector mounts this on agent containers at
/var/run/secrets/vault.hashicorp.com/serviceaccount/<path> when the volume is not mounted on app containers.
See hashicorp/vault-k8s agent.DefaultServiceAccountMount + projected serviceAccountToken.path.
*/}}
{{- define "nv-config-manager.vaultAgent.projectedSATokenVolumeName" -}}
nv-config-manager-vault-agent-sa-token
{{- end -}}

{{- define "nv-config-manager.vaultAgent.jwtTokenFilePath" -}}
/var/run/secrets/vault.hashicorp.com/serviceaccount/token
{{- end -}}

{{/*
Projected service account token for Vault *Kubernetes* auth with a custom audience (ESO parity).
Agent uses method "kubernetes" + token_path
*/}}
{{- define "nv-config-manager.vaultAgent.projectedSATokenVolume" -}}
{{- if and (eq .Values.secrets.method "vault-agent") (eq (.Values.secrets.vaultAgent.autoAuthMethod | default "kubernetes") "jwt") }}
- name: {{ include "nv-config-manager.vaultAgent.projectedSATokenVolumeName" . }}
  projected:
    defaultMode: 420
    sources:
      - serviceAccountToken:
          path: token
          audience: {{ .Values.secrets.vaultAgent.serviceAccountTokenAudience | quote }}
          expirationSeconds: {{ .Values.secrets.vaultAgent.serviceAccountTokenExpirationSeconds | default 3600 }}
{{- end }}
{{- end -}}

{{/*
Vault Agent Injector annotations (merge under pod template metadata.annotations)
*/}}
{{- define "nv-config-manager.vaultAgent.injectorAnnotations" -}}
{{- if eq .Values.secrets.method "vault-agent" }}
vault.hashicorp.com/agent-inject: "true"
vault.hashicorp.com/agent-configmap: {{ printf "%s-vault-agent" (include "nv-config-manager.fullname" .) }}
vault.hashicorp.com/agent-pre-populate: {{ .Values.secrets.vaultAgent.prePopulate | default true | toString | quote }}
{{- /* Injector default is false (agent init runs last); we always run first so user initContainers can read /vault/secrets. */}}
vault.hashicorp.com/agent-init-first: "true"
vault.hashicorp.com/log-level: {{ .Values.secrets.vaultAgent.logLevel | default "info" | quote }}
{{- if .Values.secrets.vault.namespace }}
vault.hashicorp.com/namespace: {{ .Values.secrets.vault.namespace | quote }}
{{- end }}
{{- if and (eq .Values.secrets.method "vault-agent") (eq (.Values.secrets.vaultAgent.autoAuthMethod | default "kubernetes") "jwt") }}
vault.hashicorp.com/agent-service-account-token-volume-name: {{ include "nv-config-manager.vaultAgent.projectedSATokenVolumeName" . }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Same as injectorAnnotations plus agent-pre-populate-only for batch Jobs. Without this,
the Vault sidecar keeps running after the main container exits and the Job never
reaches Complete (hits activeDeadlineSeconds).
*/}}
{{- define "nv-config-manager.vaultAgent.injectorAnnotationsJob" -}}
{{- include "nv-config-manager.vaultAgent.injectorAnnotations" . }}
{{- if eq .Values.secrets.method "vault-agent" }}
vault.hashicorp.com/agent-pre-populate-only: "true"
{{- end }}
{{- end -}}

{{/*
File paths for secrets.method=vault-agent: the injector mounts volume "vault-secrets" at
/vault/secrets on every container at admission.
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniPathApp" -}}
{{- if eq .Values.secrets.method "vault-agent" -}}
/vault/secrets/nv-config-manager.ini
{{- else -}}
/etc/nv-config-manager/nv-config-manager.ini
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configManagerIniPathDhcp" -}}
{{- if eq .Values.secrets.method "vault-agent" -}}
/vault/secrets/nv-config-manager.ini
{{- else -}}
/etc/vault/nv-config-manager.ini
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configSecretsFilePath" -}}
{{- $root := .root -}}
{{- $path := .path -}}
{{- if eq $root.Values.secrets.method "vault-agent" -}}
/vault/secrets/config-secrets.ini
{{- else -}}
{{- $path -}}
{{- end -}}
{{- end -}}

{{/*
nv-config-manager.ini volume mount: ESO secret dir vs Vault Agent (injector provides /vault/secrets)
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniVolumeMount" -}}
{{- if ne .Values.secrets.method "vault-agent" }}
- name: nv-config-manager-config
  mountPath: /etc/nv-config-manager
  readOnly: true
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configManagerIniVolume" -}}
{{- if ne .Values.secrets.method "vault-agent" }}
- name: nv-config-manager-config
  secret:
    secretName: {{ include "nv-config-manager.iniSecretName" . }}
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.bmcVolumeMount" -}}
{{- if and .Values.temporal.redfish.enabled (ne .Values.secrets.method "vault-agent") }}
- name: bmc-creds
  mountPath: /etc/vault/bmc-creds.json
  subPath: bmc-creds.json
  readOnly: true
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.bmcVolume" -}}
{{- if and .Values.temporal.redfish.enabled (ne .Values.secrets.method "vault-agent") }}
- name: bmc-creds
  secret:
    secretName: bmc-creds
{{- end }}
{{- end -}}

{{/*
DHCP mounts nv-config-manager.ini under /etc/vault (legacy path); vault-agent uses /vault/secrets/nv-config-manager.ini only.
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniFileMountEtcVault" -}}
{{- if ne .Values.secrets.method "vault-agent" }}
- name: nv-config-manager-config
  mountPath: /etc/vault/nv-config-manager.ini
  subPath: nv-config-manager.ini
  readOnly: true
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configManagerSecretsDirEtcVault" -}}
{{- if ne .Values.secrets.method "vault-agent" }}
- name: nv-config-manager-config
  mountPath: /etc/vault
  readOnly: true
{{- end }}
{{- end -}}

{{/*
config-secrets.ini (render consumer mounts)
Usage: include "nv-config-manager.vaultAgent.configSecretsVolumeMount" (dict "root" . "mountPath" .Values.renderService.configSecrets.mountPath)
*/}}
{{- define "nv-config-manager.vaultAgent.configSecretsVolumeMount" -}}
{{- $root := .root -}}
{{- $mp := .mountPath -}}
{{- if ne $root.Values.secrets.method "vault-agent" }}
- name: config-secrets
  mountPath: {{ $mp | quote }}
  readOnly: true
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configSecretsVolume" -}}
{{- if ne .Values.secrets.method "vault-agent" }}
- name: config-secrets
  secret:
    secretName: {{ include "nv-config-manager.networkSecretsName" . }}
{{- end }}
{{- end -}}

{{/*
Consul-template: KV v2 user key from secret() result.
Use `.Data` then `index ... "data"` for the KV v2 payload (same as HashiCorp *api.Secret).
Usage: ctKv2Key emits a full consul-template action (including outer braces). In Helm use {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict ...) }} once — do not wrap the include in extra template delimiters.
*/}}
{{- define "nv-config-manager.vaultAgent.ctKv2Key" -}}
{{- $vn := required "var is required" .var -}}
{{- $vk := .key | toString -}}
{{- printf "{{if $%s}}{{if $%s.Data}}{{index (or (index $%s.Data \"data\") (index $%s.Data \"Data\")) %q}}{{end}}{{end}}" $vn $vn $vn $vn $vk -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.ctKv2ShellExport" -}}
{{- $vn := required "var is required" .var -}}
{{- $vk := .key | toString -}}
{{- $env := required "env is required" .env -}}
{{- $marker := printf "__NV_CONFIG_MANAGER_%s__" $env -}}
{{- printf "%s=$(cat <<'%s'\n%s\n%s\n)\nexport %s\n" $env $marker (include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" $vn "key" $vk)) $marker $env -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.ctKv2OptionalIniLine" -}}
{{- $vn := required "var is required" .var -}}
{{- $vk := .key | toString -}}
{{- $name := required "name is required" .name -}}
{{- printf "{{with $%s}}{{with $%s.Data}}{{with index (or (index $%s.Data \"data\") (index $%s.Data \"Data\")) %q}}%s = {{.}}{{end}}{{end}}{{end}}" $vn $vn $vn $vn $vk $name -}}
{{- end -}}

{{/*
Consul-template prelude: declare $secret vars for nv-config-manager.ini (same KV paths as ESO unified ExternalSecret)
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniPrelude" -}}
{{- $root := . -}}
{{- $m := include "nv-config-manager.vault.kvMountPath" $root -}}
{{- $dcimProvider := $root.Values.dcim.provider | default "nautobot-2x" -}}
{{- if eq $dcimProvider "nautobot-2x" -}}
{{- $nbp := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "nautobot") -}}
{{- printf "{{- $nautobot := secret %q -}}\n" (printf "%s/data/%s" $m $nbp) -}}
{{- else -}}
{{- $dp := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "dcim") -}}
{{- printf "{{- $dcim := secret %q -}}\n" (printf "%s/data/%s" $m $dp) -}}
{{- if eq $root.Values.externalServices.nats.authMethod "password" -}}
{{- $np := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "nats") -}}
{{- printf "{{- $nats := secret %q -}}\n" (printf "%s/data/%s" $m $np) -}}
{{- end -}}
{{- end -}}
{{- if $root.Values.externalServices.redis.passwordAuth -}}
{{- $rp := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "redis") -}}
{{- printf "{{- $redis := secret %q -}}\n" (printf "%s/data/%s" $m $rp) -}}
{{- end -}}
{{- $pg := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "postgres") -}}
{{- printf "{{- $postgres := secret %q -}}\n" (printf "%s/data/%s" $m $pg) -}}
{{- if dig "slack" "channel" "" $root.Values.externalServices -}}
{{- $sp := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "slack") -}}
{{- printf "{{- $slack := secret %q -}}\n" (printf "%s/data/%s" $m $sp) -}}
{{- end -}}
{{- if $root.Values.temporal.enabled -}}
{{- $np := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "network") -}}
{{- printf "{{- $network := secret %q -}}\n" (printf "%s/data/%s" $m $np) -}}
{{- $rf := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "redfish") -}}
{{- printf "{{- $redfish := secret %q -}}\n" (printf "%s/data/%s" $m $rf) -}}
{{- end -}}
{{- if $root.Values.networkDhcp.enabled -}}
{{- printf "{{- $leasedb := secret %q -}}\n" (printf "%s/data/%s" $m $pg) -}}
{{- end -}}
{{- /* Same guard as vault-secrets.yaml unified ExternalSecret data + [jira] section */ -}}
{{- if and $root.Values.temporal.enabled (index $root.Values.secrets.vault.paths "jira") -}}
{{- $jpath := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "jira") -}}
{{- printf "{{- $jira := secret %q -}}\n" (printf "%s/data/%s" $m $jpath) -}}
{{- end -}}
{{- end -}}

{{/*
nv-config-manager.ini body (consul-template): must stay in sync with vault-secrets.yaml unified ExternalSecret template
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniBody" -}}
{{- $root := . -}}
{{- $dcimProvider := $root.Values.dcim.provider | default "nautobot-2x" -}}
{{- $dhcpName := include "nv-config-manager.componentName" (dict "root" $root "component" "dhcp") -}}
{{- $configStoreName := include "nv-config-manager.componentName" (dict "root" $root "component" "config-store") -}}
{{- $temporalName := include "nv-config-manager.componentName" (dict "root" $root "component" "temporal") -}}
{{- $renderName := include "nv-config-manager.componentName" (dict "root" $root "component" "render") -}}
{{- $ztpName := include "nv-config-manager.componentName" (dict "root" $root "component" "ztp") -}}
{{- $internalPort := 9000 -}}
          # =================================================================
          # NVIDIA Config Manager Unified Configuration
          # Auto-generated from Helm chart (Vault Agent)
          # Environment: {{ $root.Values.global.environment }}
          # =================================================================

          {{ include "nv-config-manager.authIniSections" $root | nindent 10 }}

          # -----------------------------------------------------------------
          # Nautobot Configuration (shared by all services)
          # -----------------------------------------------------------------
          {{- if eq $dcimProvider "nautobot-2x" }}
          [nautobot]
          server = {{ include "nv-config-manager.nautobotServer" $root }}
          public_url = {{ include "nv-config-manager.nautobotPublicUrl" $root }}
          token = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "nautobot" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "nautobot" "key" "token"))) }}
          version = {{ $root.Values.externalServices.nautobot.version }}
          {{- if $root.Values.externalServices.nautobot.maxWorkers }}
          max_workers = {{ $root.Values.externalServices.nautobot.maxWorkers }}
          {{- end }}
          {{- if $root.Values.externalServices.nautobot.retries }}
          retries = {{ $root.Values.externalServices.nautobot.retries }}
          {{- end }}
          verify = {{ $root.Values.externalServices.nautobot.verify }}
          cache_refresh_interval = {{ $root.Values.externalServices.nautobot.cacheRefreshInterval }}
          cache_ttl = {{ $root.Values.externalServices.nautobot.cacheTtl }}
          {{- end }}

          # -----------------------------------------------------------------
          # DCIM Provider Configuration (canonical shared connection settings)
          # -----------------------------------------------------------------
          [dcim]
          provider = {{ $root.Values.dcim.provider | default "nautobot-2x" }}
          server = {{ include "nv-config-manager.dcimServer" $root }}
          public_url = {{ include "nv-config-manager.dcimPublicUrl" $root }}
          {{- if eq $dcimProvider "nautobot-2x" }}
          token = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "nautobot" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "nautobot" "key" "token"))) }}
          {{- else }}
          token = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "dcim" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "dcim" "key" "token"))) }}
          {{- end }}
          verify = {{ include "nv-config-manager.dcimVerify" $root }}
          cache_refresh_interval = {{ include "nv-config-manager.dcimCacheRefreshInterval" $root }}
          cache_ttl = {{ include "nv-config-manager.dcimCacheTtl" $root }}

          [dcim.options]
          {{- range $key, $value := $root.Values.dcim.options }}
          {{ $key }} = {{ $value }}
          {{- end }}

          # -----------------------------------------------------------------
          # NATS Configuration (message queue for render service)
          # -----------------------------------------------------------------
          [nats]
          server = {{ include "nv-config-manager.natsServer" $root }}
          queue = {{ $root.Values.externalServices.nats.queue }}
          auth_method = {{ $root.Values.externalServices.nats.authMethod }}
          {{- if eq $root.Values.externalServices.nats.authMethod "password" }}
          user = {{ $root.Values.externalServices.nats.user }}
          {{- if eq $dcimProvider "nautobot-2x" }}
          password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "nautobot" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "nautobot" "key" "natsPassword"))) }}
          {{- else }}
          password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "nats" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "nats" "key" "password"))) }}
          {{- end }}
          {{- else if eq $root.Values.externalServices.nats.authMethod "JWT" }}
          creds_path = {{ $root.Values.externalServices.nats.credsPath }}
          {{- end }}
          local = {{ $root.Values.externalServices.nats.local }}
          config_manager_stream = {{ $root.Values.externalServices.nats.streams.configManager.name }}
          config_manager_api_prefix = {{ $root.Values.externalServices.nats.streams.configManager.apiPrefix | default "$JS.API" }}
          config_manager_consumer_name = {{ include "nv-config-manager.configManagerConsumerName" $root }}
          config_manager_subjects = {{ join "," $root.Values.externalServices.nats.streams.configManager.subjects }}
          render_change_stream = {{ $root.Values.externalServices.nats.streams.configManager.name }}
          render_change_subject = {{ $root.Values.externalServices.nats.streams.configManager.renderChangeSubject }}
          device_change_stream = {{ $root.Values.externalServices.nats.streams.configManager.name }}
          device_change_subject = {{ $root.Values.externalServices.nats.streams.configManager.deviceChangeSubject }}
          archive_stream = {{ $root.Values.externalServices.nats.streams.configManager.name }}
          archive_subject = {{ $root.Values.externalServices.nats.streams.configManager.archiveSubject }}
          archive_consumer_name = {{ include "nv-config-manager.archiveConsumerName" $root }}
          archive_deliver_subject = {{ include "nv-config-manager.archiveConsumerDeliverySubject" $root }}
          nautobot_stream = {{ $root.Values.externalServices.nats.streams.nautobot.name }}
          nautobot_api_prefix = {{ $root.Values.externalServices.nats.streams.nautobot.apiPrefix | default "$JS.API" }}
          nautobot_consumer_name = {{ include "nv-config-manager.nautobotConsumerName" $root }}
          nautobot_subjects = {{ join "," $root.Values.externalServices.nats.streams.nautobot.subjects }}
          nautobot_subject = {{ $root.Values.externalServices.nats.streams.nautobot.subject }}
          dcim_change_stream = {{ $root.Values.dcim.events.stream | default $root.Values.externalServices.nats.streams.nautobot.name }}
          dcim_change_subject = {{ $root.Values.dcim.events.subject | default $root.Values.externalServices.nats.streams.nautobot.subject }}

          # -----------------------------------------------------------------
          # Redis Configuration (shared by all services)
          # -----------------------------------------------------------------
          [redis]
          host = {{ include "nv-config-manager.redisHost" $root }}
          port = {{ $root.Values.externalServices.redis.port }}
          db = {{ $root.Values.externalServices.redis.db }}
          lock_db = {{ $root.Values.externalServices.redis.lockDb }}
          ssl = {{ $root.Values.externalServices.redis.ssl }}
          socket_timeout = {{ $root.Values.externalServices.redis.socketTimeout }}
          socket_connect_timeout = {{ $root.Values.externalServices.redis.socketConnectTimeout }}
          {{- if $root.Values.externalServices.redis.passwordAuth }}
          password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redis" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redis" "key" "password"))) }}
          {{- end }}


          # -----------------------------------------------------------------
          # Config Store Database Configuration
          # -----------------------------------------------------------------
          [config_store]
          database_host = {{ tpl (required "externalServices.postgres.configStore.host is required" $root.Values.externalServices.postgres.configStore.host) $root }}
          database_port = {{ $root.Values.externalServices.postgres.port }}
          database = {{ $root.Values.externalServices.postgres.configStore.database }}
          database_user = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "postgres" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "postgres" "key" "configStoreUser"))) }}
          database_password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "postgres" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "postgres" "key" "configStorePassword"))) }}

          # -----------------------------------------------------------------
          # Config Store API Configuration
          # -----------------------------------------------------------------
          [config_store.api]
          # CORS origins allowed to make cross-origin requests with credentials
          cors_origins = https://{{ $root.Values.gateway.baseHostname }}
          {{- if $root.Values.configStore.gateway.api.allowedGroups }}
          allowed_groups = {{ $root.Values.configStore.gateway.api.allowedGroups | join "," }}
          {{- end }}

          # -----------------------------------------------------------------
          # Config Store Client Configuration (for all services to fetch configs)
          # -----------------------------------------------------------------
          [config_store.client]
          # Internal endpoint for service-to-service calls (within cluster)
          # Uses sidecar port when auth sidecars are enabled for header injection
          api_service = http://{{ $configStoreName }}-api:{{ $internalPort }}
          # External API endpoint for clients outside the cluster
          api_url = https://{{ tpl $root.Values.configStore.gateway.api.hostname $root }}
          # Unified UI base URL for links (DCIM references, etc.)
          ui_url = https://{{ $root.Values.gateway.baseHostname }}
          # Set to true for internal cluster communication (uses api_service)
          # Set to false for external mTLS communication (uses api_url)
          use_internal_endpoint = {{ $root.Values.configStore.client.useInternalEndpoint | default true }}
          verify = {{ $root.Values.configStore.client.verify | default true }}

          {{- if $root.Values.temporal.enabled }}
          # -----------------------------------------------------------------
          # Temporal/Workflow Configuration
          # -----------------------------------------------------------------
          [temporal]
          # Internal: Temporal gRPC frontend (for workers, SDK clients)
          grpc_service = {{ include "nv-config-manager.temporalGrpcAddress" $root }}
          namespace = {{ $root.Values.temporal.client.namespace | default "default" }}
          tls_enabled = {{ $root.Values.temporal.client.tls.enabled | default false }}
          {{- if $root.Values.temporal.client.tls.serverName }}
          tls_server_name = {{ include "nv-config-manager.temporalTLSServerName" $root }}
          {{- end }}
          tls_ca_cert_path = /var/run/secrets/temporal-client-tls/ca.crt
          tls_client_cert_path = /var/run/secrets/temporal-client-tls/tls.crt
          tls_client_key_path = /var/run/secrets/temporal-client-tls/tls.key
          # Internal: NVIDIA Config Manager Temporal API (for internal service calls)
          # Uses sidecar port when auth sidecars are enabled for header injection
          api_service = http://{{ $temporalName }}-api:{{ $internalPort }}
          # External: Gateway URLs for user-facing links
          api_url = https://{{ tpl $root.Values.temporal.gateway.api.hostname $root }}
          temporal_ui_url = https://{{ tpl $root.Values.temporal.gateway.devUi.hostname $root }}
          ui_url = https://{{ $root.Values.gateway.baseHostname }}
          # Set to true for internal cluster communication (uses api_service)
          # Set to false for external mTLS communication (uses api_url)
          use_internal_endpoint = {{ $root.Values.temporal.client.useInternalEndpoint | default true }}

          [temporal.elasticsearch]
          local = {{ $root.Values.externalServices.elasticsearch.local }}
          server = {{ tpl $root.Values.externalServices.elasticsearch.server $root }}
          {{- if $root.Values.externalServices.elasticsearch.domain }}
          domain = {{ $root.Values.externalServices.elasticsearch.domain }}
          {{- end }}

          # -----------------------------------------------------------------
          # Temporal API Configuration (REST API for workflow operations)
          # -----------------------------------------------------------------
          [temporal.api]
          # CORS origins allowed to make cross-origin requests with credentials
          cors_origins = https://{{ $root.Values.gateway.baseHostname }},https://{{ tpl $root.Values.temporal.gateway.devUi.hostname $root }}
          {{- if $root.Values.temporal.gateway.api.allowedGroups }}
          allowed_groups = {{ $root.Values.temporal.gateway.api.allowedGroups | join "," }}
          {{- end }}

          # -----------------------------------------------------------------
          # Device Credentials (for Temporal workflows)
          # -----------------------------------------------------------------
          [device]
          username = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "network" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "network" "key" "user"))) }}
          password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "network" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "network" "key" "password"))) }}

          {{- if $root.Values.temporal.redfish.enabled }}
          # -----------------------------------------------------------------
          # Redfish/BMC Configuration (for Temporal workflows)
          # -----------------------------------------------------------------
          [redfish]
          lenovo_default_user = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "lenovoDefaultUser"))) }}
          lenovo_default_password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "lenovoDefaultPassword"))) }}
          lenovo_config_manager_password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "lenovoConfigManagerPassword"))) }}
          bluefield_default_user = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "bluefieldDefaultUser"))) }}
          bluefield_default_password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "bluefieldDefaultPassword"))) }}
          bluefield_config_manager_password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "redfish" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "redfish" "key" "bluefieldConfigManagerPassword"))) }}
          {{- end }}
          {{- end }}

          {{- if $root.Values.renderService.enabled }}
          # -----------------------------------------------------------------
          # Render Service Configuration
          # -----------------------------------------------------------------
          [render]
          # Internal endpoint for service-to-service calls
          # Uses sidecar port when auth sidecars are enabled for header injection
          api_service = http://{{ $renderName }}-api:{{ $internalPort }}
          # External URL for user-facing links
          api_url = https://{{ tpl $root.Values.renderService.gateway.hostname $root }}
          # Set to true for internal cluster communication (uses api_service)
          # Set to false for external mTLS communication (uses api_url)
          use_internal_endpoint = {{ $root.Values.renderService.client.useInternalEndpoint | default true }}
          {{- if $root.Values.renderService.gateway.allowedGroups }}
          allowed_groups = {{ $root.Values.renderService.gateway.allowedGroups | join "," }}
          {{- end }}
          {{- end }}

          # -----------------------------------------------------------------
          # Aggregate Environment Configuration
          # -----------------------------------------------------------------
          [aggregate]
          is_aggregate_environment = {{ $root.Values.global.aggregate }}

          {{- $slackChannel := dig "slack" "channel" "" $root.Values.externalServices }}
          {{- if $slackChannel }}
          # -----------------------------------------------------------------
          # Slack Configuration (shared notifications)
          # -----------------------------------------------------------------
          [slack]
          bot_token = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "slack" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "slack" "key" "token"))) }}
          channel_name = {{ $slackChannel }}
          {{- end }}

          {{- if $root.Values.networkDhcp.enabled }}
          # -----------------------------------------------------------------
          # DHCP Service Configuration
          # -----------------------------------------------------------------
          [dhcp]
          # Internal endpoint for service-to-service calls
          # Uses sidecar port when auth sidecars are enabled for header injection
          api_service = http://{{ $dhcpName }}-internal:{{ $internalPort }}
          # External URL for user-facing links
          api_url = https://{{ tpl $root.Values.networkDhcp.gateway.hostname $root }}
          cors_origins = https://{{ $root.Values.gateway.baseHostname }}
          {{- if $root.Values.networkDhcp.gateway.allowedGroups }}
          allowed_groups = {{ $root.Values.networkDhcp.gateway.allowedGroups | join "," }}
          {{- end }}

          [dhcp.kea]
          server = {{ $dhcpName }}-internal
          port = 8000

          [dhcp.lease_db]
          local = false
          host = {{ tpl $root.Values.externalServices.postgres.dhcp.host $root }}
          port = {{ $root.Values.externalServices.postgres.port }}
          database = {{ $root.Values.externalServices.postgres.dhcp.database }}
          user = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "leasedb" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "postgres" "key" "dhcpUser"))) }}
          password = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "leasedb" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "postgres" "key" "dhcpPassword"))) }}
          {{- end }}

          {{- if $root.Values.networkZtp.enabled }}
          # -----------------------------------------------------------------
          # ZTP Service Configuration
          # -----------------------------------------------------------------
          [ztp]
          # Internal endpoint for service-to-service calls
          # Uses sidecar port when auth sidecars are enabled for header injection
          api_service = http://{{ $ztpName }}-api:{{ $internalPort }}
          # External URL for user-facing links
          api_url = https://{{ tpl $root.Values.networkZtp.gateway.hostname $root }}
          # User domain for workflow context
          user_domain = {{ $root.Values.global.environment }}.{{ $root.Values.global.baseDomain }}
          # Set to true for internal cluster communication (uses api_service)
          # Set to false for external mTLS communication (uses api_url)
          use_internal_endpoint = {{ $root.Values.networkZtp.client.useInternalEndpoint | default true }}
          {{- if $root.Values.networkZtp.gateway.allowedGroups }}
          allowed_groups = {{ $root.Values.networkZtp.gateway.allowedGroups | join "," }}
          {{- end }}
{{ include "nv-config-manager.networkZtpIniStorageConfig" $root | indent 10 }}
{{ include "nv-config-manager.networkZtpIniDownloadConfig" $root | indent 10 }}
{{ include "nv-config-manager.vaultAgent.ztpS3IniConfig" $root | indent 10 }}

          {{- end }}

          {{- if $root.Values.mcp.enabled }}
          # -----------------------------------------------------------------
          # MCP Service Configuration
          # -----------------------------------------------------------------
          [mcp]
          # When auth is enabled, MCP forwards the caller's inbound Bearer token
          # to Config Manager APIs. It does not fall back to service-to-service auth.
          use_internal_endpoints = {{ $root.Values.mcp.client.useInternalEndpoints | default true }}
          max_response_bytes = {{ $root.Values.mcp.client.maxResponseBytes | default 100000 }}
          {{- if eq $dcimProvider "nautobot-2x" }}
          {{ include "nv-config-manager.vaultAgent.ctKv2OptionalIniLine" (dict "var" "nautobot" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "nautobot" "key" "readOnlyToken")) "name" "nautobot_read_only_token") }}
          # auto resolves to jwt for bundled/local Nautobot and the MCP read-only token for external Nautobot.
          nautobot_auth_mode = {{ $root.Values.mcp.nautobot.authMode | default "auto" }}
          nautobot_token_fallback_enabled = {{ $root.Values.mcp.nautobot.tokenFallbackEnabled | default false }}
          {{- end }}

          {{- end }}

          {{- if and $root.Values.temporal.enabled (index $root.Values.secrets.vault.paths "jira") }}
          # -----------------------------------------------------------------
          # Jira Configuration (for DiagnosticsWorkflow)
          # -----------------------------------------------------------------
          [jira]
          base_url  = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "jira" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "jira" "key" "baseUrl"))) }}
          api_token = {{ include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "jira" "key" (include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "jira" "key" "apiToken"))) }}
          {{- end }}

          {{- if and $root.Values.customConfig.enabled $root.Values.customConfig.iniSnippets }}
          # -----------------------------------------------------------------
          # Custom Configuration Snippets
          # -----------------------------------------------------------------
          # These snippets are defined in customConfig.iniSnippets and may
          # reference custom vault secrets from customConfig.vaultSecrets
          # -----------------------------------------------------------------
          {{- range $root.Values.customConfig.iniSnippets }}
{{ tpl . $root | indent 10 }}
          {{- end }}
          {{- end }}
{{- end -}}

{{/*
config-secrets.ini: one $ variable per site/region (sanitized identifier)
*/}}
{{- define "nv-config-manager.vaultAgent.configSecretsPrelude" -}}
{{- $root := . -}}
{{- $cm := include "nv-config-manager.vault.configKvMountPath" $root -}}
{{- range $site := $root.Values.secrets.vault.configSecrets.sites }}
{{- $p := required (printf "configSecrets.sites[].path is required for site %s" $site.name) $site.path }}
{{- $vn := printf "site_%s" (regexReplaceAll "[^a-zA-Z0-9_]" $site.name "_") }}
{{- printf "{{- $%s := secret %q -}}\n" $vn (printf "%s/data/%s" $cm $p) -}}
{{- end }}
{{- range $region := $root.Values.secrets.vault.configSecrets.regions }}
{{- $p := required (printf "configSecrets.regions[].path is required for region %s" $region.name) $region.path }}
{{- $vn := printf "region_%s" (regexReplaceAll "[^a-zA-Z0-9_]" $region.name "_") }}
{{- printf "{{- $%s := secret %q -}}\n" $vn (printf "%s/data/%s" $cm $p) -}}
{{- end }}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configSecretsIniBody" -}}
{{- $root := . -}}
{{- range $site := $root.Values.secrets.vault.configSecrets.sites }}
          {{- printf "\n[site.%s]" $site.name }}
          {{- $vn := printf "site_%s" (regexReplaceAll "[^a-zA-Z0-9_]" $site.name "_") }}
          {{- printf "{{- if $%s -}}{{if $%s.Data}}{{range $k, $v := (or (index $%s.Data \"data\") (index $%s.Data \"Data\"))}}\n" $vn $vn $vn $vn }}
          {{- printf "{{ $k }}: {{ $v | indent 2 }}\n" }}
          {{- printf "{{- end }}{{ end }}{{ end }}\n" }}
{{- end }}
{{- range $region := $root.Values.secrets.vault.configSecrets.regions }}
          {{- printf "\n[region.%s]" $region.name }}
          {{- $vn := printf "region_%s" (regexReplaceAll "[^a-zA-Z0-9_]" $region.name "_") }}
          {{- printf "{{- if $%s -}}{{if $%s.Data}}{{range $k, $v := (or (index $%s.Data \"data\") (index $%s.Data \"Data\"))}}\n" $vn $vn $vn $vn }}
          {{- printf "{{ $k }}: {{ $v | indent 2 }}\n" }}
          {{- printf "{{- end }}{{ end }}{{ end }}\n" }}
{{- end }}
{{- end -}}

{{/*
Consul-template bodies as separate ConfigMap files under /vault/configs (see vault-agent-configmap.yaml).
Using template { source = ... } avoids HCL <<-EOT heredocs, which break if generated INI lines confuse the parser.
*/}}
{{- define "nv-config-manager.vaultAgent.configManagerIniTplFile" -}}
{{- include "nv-config-manager.vaultAgent.configManagerIniPrelude" . -}}
{{- include "nv-config-manager.vaultAgent.configManagerIniBody" . -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.configSecretsTplFile" -}}
{{- include "nv-config-manager.vaultAgent.configSecretsPrelude" . -}}
{{- include "nv-config-manager.vaultAgent.configSecretsIniBody" . -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.ztpS3IniConfig" -}}
{{- $root := . -}}
{{- $s3 := $root.Values.networkZtp.storage.s3 | default dict -}}
{{- if eq (include "nv-config-manager.networkZtp.s3VaultAgentIniEnabled" $root) "true" -}}
{{- $m := include "nv-config-manager.vault.kvMountPath" $root -}}
{{- $p := include "nv-config-manager.vault.secretPath" (dict "root" $root "secret" "ztpS3") -}}
{{- printf "{{- $ztpS3 := secret %q -}}\n" (printf "%s/data/%s" $m $p) -}}
{{- $endpointKey := include "nv-config-manager.vault.configuredKeyName" (dict "root" $root "secret" "ztpS3" "key" "endpoint") -}}
{{- if and (not $s3.endpoint) $endpointKey -}}
{{- printf "s3_endpoint = %s\n" (include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "ztpS3" "key" $endpointKey)) -}}
{{- end -}}
{{- $accessKey := include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "ztpS3" "key" "accessKeyId") -}}
{{- $secretKey := include "nv-config-manager.vault.keyName" (dict "root" $root "secret" "ztpS3" "key" "secretAccessKey") -}}
{{- printf "s3_access_key = %s\n" (include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "ztpS3" "key" $accessKey)) -}}
{{- printf "s3_secret_key = %s\n" (include "nv-config-manager.vaultAgent.ctKv2Key" (dict "var" "ztpS3" "key" $secretKey)) -}}
{{- end -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.bmcJsonTplFile" -}}
{{- $m := include "nv-config-manager.vault.kvMountPath" . -}}
{{- $bp := include "nv-config-manager.vault.secretPath" (dict "root" . "secret" "bmc") -}}
{{- $bk := include "nv-config-manager.vault.keyName" (dict "root" . "secret" "bmc" "key" "credsJson") -}}
{{- $path := printf "%s/data/%s" $m $bp -}}
{{- printf "{{- $bmc := secret %q -}}\n{{- if $bmc }}{{ if $bmc.Data }}{{ index (or (index $bmc.Data \"data\") (index $bmc.Data \"Data\")) %q }}{{ end }}{{ end }}\n" $path $bk -}}
{{- end -}}

{{- define "nv-config-manager.vaultAgent.agentHcl" -}}
pid_file = "/home/vault/pidfile"

vault {
  address = {{ .Values.secrets.vault.server | quote }}
{{- if .Values.secrets.vault.namespace }}
  namespace = {{ .Values.secrets.vault.namespace | quote }}
{{- end }}
}

auto_auth {
{{- if and (eq (.Values.secrets.vaultAgent.autoAuthMethod | default "kubernetes") "jwt") }}
  method "kubernetes" {
    mount_path = {{ required "secrets.vault.mountPath is required for Vault Agent" .Values.secrets.vault.mountPath | quote }}
{{- if .Values.secrets.vault.namespace }}
    namespace  = {{ .Values.secrets.vault.namespace | quote }}
{{- end }}
    config = {
      role       = {{ .Values.secrets.vault.role | quote }}
      token_path = {{ include "nv-config-manager.vaultAgent.jwtTokenFilePath" . | quote }}
    }
  }
{{- else }}
  method "kubernetes" {
    mount_path = {{ required "secrets.vault.mountPath is required for Vault Agent" .Values.secrets.vault.mountPath | quote }}
{{- if .Values.secrets.vault.namespace }}
    namespace  = {{ .Values.secrets.vault.namespace | quote }}
{{- end }}
    config = {
      role = {{ .Values.secrets.vault.role | quote }}
    }
  }
{{- end }}
  sink {
    type = "file"
    config = {
      path = "/home/vault/.vault-token"
    }
  }
}

template {
  source      = "/vault/configs/nv-config-manager.ini.tpl"
  destination = "/vault/secrets/nv-config-manager.ini"
  error_on_missing_key = true
}
{{- if and .Values.secrets.vault.configSecrets.enabled (or .Values.secrets.vault.configSecrets.sites .Values.secrets.vault.configSecrets.regions) }}

template {
  source      = "/vault/configs/config-secrets.ini.tpl"
  destination = "/vault/secrets/config-secrets.ini"
  error_on_missing_key = true
}
{{- end }}
{{- if and .Values.temporal.enabled .Values.temporal.redfish.enabled }}

template {
  source      = "/vault/configs/bmc-creds.json.tpl"
  destination = "/vault/secrets/bmc-creds.json"
  error_on_missing_key = true
}
{{- end }}
{{- end -}}

{{/*
Injector init container runs: vault agent -config=/vault/configs/config-init.hcl
Sidecar runs: vault agent -config=/vault/configs/config.hcl
Both files must exist in the ConfigMap (hashicorp/vault-k8s).
*/}}
{{- define "nv-config-manager.vaultAgent.agentHclInit" }}
{{ include "nv-config-manager.vaultAgent.agentHcl" . }}
exit_after_auth = true
{{- end }}
