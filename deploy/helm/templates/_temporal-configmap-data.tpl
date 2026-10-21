{{/*
=============================================================================
Temporal ConfigMap Data Templates
=============================================================================
Named templates for Temporal ConfigMap data sections, used in both ConfigMap
definitions and Deployment checksum annotations. This ensures pods
automatically restart when ConfigMap content changes.

Usage in ConfigMap:
  data:
  {{- include "nv-config-manager.configmap.<name>" . | nindent 2 }}

Usage in Deployment annotation:
  checksum/<name>: {{ include "nv-config-manager.configmap.<name>" . | sha256sum }}
=============================================================================
*/}}

{{/*
RBAC with workflow plugin rows appended to rbac.workflows.
Plugin rows add to the platform rows. A repeated workflow name fails the render
so that two sources never disagree about the roles of one workflow.
*/}}
{{- define "nv-config-manager.mergedRbac" -}}
{{- $rbac := deepCopy .Values.rbac -}}
{{- $pluginRows := .Values.temporal.workflowPlugins.rbac | default list -}}
{{- $names := dict -}}
{{- range ($rbac.workflows | default list) -}}
{{- $_ := set $names .name true -}}
{{- end -}}
{{- range $pluginRows -}}
{{- if not (and .name (hasKey . "read_roles") (hasKey . "execute_roles")) -}}
{{- fail "temporal.workflowPlugins.rbac[] entries require name, read_roles, and execute_roles" -}}
{{- end -}}
{{- if hasKey $names .name -}}
{{- fail (printf "temporal.workflowPlugins.rbac repeats workflow %q, which rbac.workflows already defines" .name) -}}
{{- end -}}
{{- $_ := set $names .name true -}}
{{- end -}}
{{- if $pluginRows -}}
{{- $_ := set $rbac "workflows" (concat ($rbac.workflows | default list) $pluginRows) -}}
{{- end -}}
{{- toYaml $rbac -}}
{{- end -}}

{{/*
Temporal RBAC ConfigMap data
*/}}
{{- define "nv-config-manager.configmap.temporal-rbac" -}}
rbac.yaml: |
{{- include "nv-config-manager.mergedRbac" . | nindent 2 }}
{{- end -}}

{{/*
Temporal server ConfigMap data (PostgreSQL configuration)
*/}}
{{- define "nv-config-manager.configmap.temporal-config" -}}
config_template.yaml: |-
  log:
    stdout: true
    level: DEBUG

  persistence:
    numHistoryShards: {{ .Values.temporal.services.history.replicas }}
    defaultStore: default
    visibilityStore: visibility
    datastores:
      default:
        sql:
          pluginName: "postgres12"
          databaseName: "{{ .Values.externalServices.postgres.temporal.database }}"
          connectAddr: {{ `{{ default .Env.POSTGRES_HOST "127.0.0.1" }}` }}:{{ .Values.externalServices.postgres.port }}
          connectProtocol: "tcp"
          user: {{ `{{ default .Env.POSTGRES_USER "temporal" }}` }}
          password: {{ `{{ default .Env.POSTGRES_PASS "temporal" }}` }}
          maxConns: 20
          maxIdleConns: 20
          maxConnLifetime: 1h
          tls:
            enabled: false
      visibility:
        sql:
          pluginName: "postgres12"
          databaseName: "{{ .Values.externalServices.postgres.temporal.visibilityDatabase }}"
          connectProtocol: "tcp"
          connectAddr: {{ `{{ default .Env.POSTGRES_VISIBILITY_HOST (default .Env.POSTGRES_HOST "127.0.0.1") }}` }}:{{ .Values.externalServices.postgres.port }}
          user: {{ `{{ default .Env.POSTGRES_VISIBILITY_USER (default .Env.POSTGRES_USER "temporal") }}` }}
          password: {{ `{{ default .Env.POSTGRES_VISIBILITY_PASS (default .Env.POSTGRES_PASS "temporal") }}` }}
          maxConns: 10
          maxIdleConns: 10
          maxConnLifetime: 1h
          tls:
            enabled: false

  global:
    membership:
      name: temporal
      maxJoinDuration: 30s
      broadcastAddress: {{ `{{ default .Env.POD_IP "0.0.0.0" }}` }}

    pprof:
      port: 7936

    metrics:
      tags:
        type: {{ `{{ .Env.SERVICES }}` }}
      prometheus:
        timerType: histogram
        listenAddress: "0.0.0.0:8000"

  services:
    frontend:
      rpc:
        grpcPort: {{ .Values.temporal.services.frontend.port }}
        membershipPort: {{ .Values.temporal.services.frontend.membershipPort }}
        bindOnIP: "0.0.0.0"

    history:
      rpc:
        grpcPort: {{ .Values.temporal.services.history.port }}
        membershipPort: {{ .Values.temporal.services.history.membershipPort }}
        bindOnIP: "0.0.0.0"

    matching:
      rpc:
        grpcPort: {{ .Values.temporal.services.matching.port }}
        membershipPort: {{ .Values.temporal.services.matching.membershipPort }}
        bindOnIP: "0.0.0.0"

    worker:
      rpc:
        grpcPort: {{ .Values.temporal.services.worker.port }}
        membershipPort: {{ .Values.temporal.services.worker.membershipPort }}
        bindOnIP: "0.0.0.0"

  clusterMetadata:
    enableGlobalDomain: false
    failoverVersionIncrement: 10
    masterClusterName: "active"
    currentClusterName: "active"
    clusterInformation:
      active:
        enabled: true
        initialFailoverVersion: 1
        rpcName: "temporal-frontend"
        rpcAddress: "127.0.0.1:{{ .Values.temporal.services.frontend.port }}"

  dcRedirectionPolicy:
      policy: "noop"

  archival:
    status: "disabled"

  dynamicConfigClient:
    filepath: "/etc/temporal/dynamic_config/dynamic_config.yaml"
    pollInterval: "10s"
{{- end -}}

{{/*
Temporal Dynamic Config ConfigMap data
*/}}
{{- define "nv-config-manager.configmap.temporal-dynamic-config" -}}
dynamic_config.yaml: |-
  # Dynamic configuration for Temporal
  # See: https://docs.temporal.io/references/dynamic-configuration
  {{- if .Values.temporal.dynamicConfig }}
  {{- range .Values.temporal.dynamicConfig }}
  - key: {{ .key }}
    value: {{ .value }}
    {{- if .constraints }}
    constraints: {{ toYaml .constraints | nindent 6 }}
    {{- end }}
  {{- end }}
  {{- end }}
{{- end -}}
