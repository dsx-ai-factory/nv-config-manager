module github.com/nvidia/nv-config-manager/components/nats-ready

go 1.26.0

toolchain go1.26.6

require (
	github.com/nats-io/nats.go v1.47.0
	github.com/rs/zerolog v1.34.0
)

require (
	github.com/klauspost/compress v1.18.0 // indirect
	github.com/mattn/go-colorable v0.1.13 // indirect
	github.com/mattn/go-isatty v0.0.19 // indirect
	github.com/nats-io/nkeys v0.4.11 // indirect
	github.com/nats-io/nuid v1.0.1 // indirect
	// Security: Fix SSH source-address restriction bypass (GO-2026-6303)
	golang.org/x/crypto v0.55.0 // indirect
	golang.org/x/sys v0.47.0 // indirect
)
