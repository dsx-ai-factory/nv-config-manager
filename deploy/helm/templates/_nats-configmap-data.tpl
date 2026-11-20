{{/*
=============================================================================
NATS ConfigMap Data Templates
=============================================================================
Named templates for NATS ConfigMap data sections, used in both ConfigMap
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
NATS ConfigMap data
*/}}
{{- define "nv-config-manager.configmap.nats-config" -}}
nats.conf: |
  # NATS Server Configuration
  port: 4222
  http_port: 8222
  
  max_payload: {{ printf "%.0f" .Values.nautobotNats.maxPayload }}
  {{- if include "nv-config-manager.natsGatewayHostname" . }}

  # Client protocol over WebSocket; TLS terminates at the gateway.
  websocket {
    port: {{ .Values.nautobotNats.gateway.websocket.port | default 8080 }}
    no_tls: true
  }
  {{- end }}
  
  {{- if .Values.nautobotNats.jetstream.enabled }}
  jetstream {
    store_dir: /data/jetstream
    max_mem: {{ .Values.nautobotNats.jetstream.maxMem | default "256M" }}
    max_file: {{ .Values.nautobotNats.jetstream.maxFile | default "50G" }}
  }
  {{- end }}
  
  # Single account configuration when nautobot is bundled with nv-config-manager
  # All users share the same account and JetStream
  accounts {
    main: {
      jetstream: enabled
      users: [
        {user: sys, password: $SYS_PASSWORD},
        {user: {{ include "nv-config-manager.natsUser" . }}, password: $NV_CONFIG_MANAGER_PASSWORD},
        {user: nautobot, password: $NAUTOBOT_PASSWORD}
      ]
    }
  }
{{- end -}}
