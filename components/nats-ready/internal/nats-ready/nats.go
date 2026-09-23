/*
 * SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
package natsready

import (
	"context"
	_ "embed" // Required to enable the //go:embed directives below.
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"
)

//go:embed nautobot_config.json
var nautobotConfigJSON []byte

//go:embed nv_config_manager_stream_config.json
var nvConfigManagerStreamConfigJSON []byte

// StreamConfig represents the structure of our nautobot_config.json
type StreamConfig struct {
	Config jetstream.StreamConfig `json:"config"`
}

// Runner defines the interface for running NATS readiness checks.
type Runner interface {
	Run() error
}

type NatsReadyConfig struct {
	Address                        string
	nautobotNATSConfigBytes        []byte
	nvConfigManagerNATSConfigBytes []byte
}

type natsReady struct {
	config                      *NatsReadyConfig
	js                          jetstream.JetStream
	log                         zerolog.Logger
	nc                          *nats.Conn
	nvConfigManagerStreamConfig *StreamConfig
	nautobotStreamConfig        *StreamConfig
}

func applyStreamOverrides(streamConfig *StreamConfig, nameEnv string, subjectsEnv string) {
	if name := strings.TrimSpace(os.Getenv(nameEnv)); name != "" {
		streamConfig.Config.Name = name
	}
	if rawSubjects := strings.TrimSpace(os.Getenv(subjectsEnv)); rawSubjects != "" {
		subjects := []string{}
		for _, subject := range strings.Split(rawSubjects, ",") {
			if trimmed := strings.TrimSpace(subject); trimmed != "" {
				subjects = append(subjects, trimmed)
			}
		}
		if len(subjects) > 0 {
			streamConfig.Config.Subjects = subjects
		}
	}
}

func NewRunner(config *NatsReadyConfig) (Runner, error) {
	config.nautobotNATSConfigBytes = nautobotConfigJSON
	config.nvConfigManagerNATSConfigBytes = nvConfigManagerStreamConfigJSON
	logger := log.With().
		Str("component", "nats-ready").
		Str("address", RedactAddress(config.Address)).
		Logger()

	nc, err := nats.Connect(config.Address)
	if err != nil {
		return nil, err
	}
	logger.Info().Msg("Connected to NATS server")

	js, err := jetstream.New(nc)
	if err != nil {
		nc.Close()
		return nil, err
	}
	logger.Info().Msg("JetStream initialized successfully")

	// Parse the embedded config
	var nautobotStreamConfig StreamConfig
	if err := json.Unmarshal(nautobotConfigJSON, &nautobotStreamConfig); err != nil {
		nc.Close()
		return nil, fmt.Errorf("failed to parse stream config: %w", err)
	}
	applyStreamOverrides(&nautobotStreamConfig, "NAUTOBOT_NATS_STREAM_NAME", "NAUTOBOT_NATS_STREAM_SUBJECTS")

	var nvConfigManagerStreamConfig StreamConfig
	if err := json.Unmarshal(nvConfigManagerStreamConfigJSON, &nvConfigManagerStreamConfig); err != nil {
		nc.Close()
		return nil, fmt.Errorf("failed to parse nv-config-manager stream config: %w", err)
	}
	applyStreamOverrides(
		&nvConfigManagerStreamConfig,
		"NV_CONFIG_MANAGER_NATS_STREAM_NAME",
		"NV_CONFIG_MANAGER_NATS_STREAM_SUBJECTS",
	)

	n := &natsReady{
		config:                      config,
		js:                          js,
		nc:                          nc,
		log:                         logger,
		nvConfigManagerStreamConfig: &nvConfigManagerStreamConfig,
		nautobotStreamConfig:        &nautobotStreamConfig,
	}
	return n, nil
}

// Run executes the NATS readiness check and stream setup.
func (n *natsReady) Run() error {
	defer n.nc.Close()

	ctx := context.Background()

	n.log.Info().Msg("Starting NATS readiness check")
	n.log.Info().Msgf("Using Nautobot NATS config: \n%s\n", string(n.config.nautobotNATSConfigBytes))
	n.log.Info().Msgf("Using NVIDIA Config Manager NATS config: \n%s\n", string(n.config.nvConfigManagerNATSConfigBytes))

	// Perform getOrCreate operation on the nautobot stream
	if err := n.getOrCreateStream(ctx, n.nautobotStreamConfig); err != nil {
		n.log.Error().Err(err).Msg("Failed to get or create nautobot stream")
		return fmt.Errorf("nautobot stream operation failed: %w", err)
	}

	// Perform getOrCreate operation on the nv-config-manager stream
	if err := n.getOrCreateStream(ctx, n.nvConfigManagerStreamConfig); err != nil {
		n.log.Error().Err(err).Msg("Failed to get or create nv-config-manager stream")
		return fmt.Errorf("nv-config-manager stream operation failed: %w", err)
	}

	n.log.Info().Msg("NATS readiness check completed successfully")
	return nil
}

// getOrCreateStream checks if the stream exists, creates it if not, or validates/updates it if it exists
func (n *natsReady) getOrCreateStream(ctx context.Context, streamConfig *StreamConfig) error {
	streamName := streamConfig.Config.Name
	n.log.Info().Str("stream", streamName).Msg("Checking stream existence")

	// Try to get the existing stream
	stream, err := n.js.Stream(ctx, streamName)
	if err != nil {
		if !errors.Is(err, jetstream.ErrStreamNotFound) {
			return fmt.Errorf("check stream %s: %w", streamName, err)
		}
		// Stream doesn't exist, create it
		n.log.Info().Str("stream", streamName).Msg("Stream not found, creating new stream")
		return n.createStream(ctx, streamConfig)
	}

	// Stream exists, validate and update if necessary
	n.log.Info().Str("stream", streamName).Msg("Stream found, validating configuration")
	return n.validateAndUpdateStream(ctx, stream, streamConfig)
}

// createStream creates a new stream with the configuration from config.json
func (n *natsReady) createStream(ctx context.Context, streamConfig *StreamConfig) error {
	streamName := streamConfig.Config.Name

	stream, err := n.js.CreateStream(ctx, streamConfig.Config)
	if err != nil {
		return fmt.Errorf("failed to create stream %s: %w", streamName, err)
	}

	n.log.Info().
		Str("stream", streamName).
		Strs("subjects", streamConfig.Config.Subjects).
		Msg("Stream created successfully")

	// Log stream info
	info := stream.CachedInfo()
	n.log.Info().
		Str("stream", streamName).
		Uint64("messages", info.State.Msgs).
		Uint64("bytes", info.State.Bytes).
		Msg("Stream information")

	return nil
}

// validateAndUpdateStream compares existing stream config with expected config and updates if needed
func (n *natsReady) validateAndUpdateStream(ctx context.Context, stream jetstream.Stream, streamConfig *StreamConfig) error {
	streamName := streamConfig.Config.Name
	info := stream.CachedInfo()
	currentConfig := info.Config

	// Compare configurations
	if n.configsMatch(&currentConfig, &streamConfig.Config) {
		n.log.Info().Str("stream", streamName).Msg("Stream configuration is up to date")
		return nil
	}

	// Configurations don't match, update the stream
	n.log.Warn().Str("stream", streamName).Msg("Stream configuration mismatch, updating stream")

	updatedStream, err := n.js.UpdateStream(ctx, streamConfig.Config)
	if err != nil {
		return fmt.Errorf("failed to update stream %s: %w", streamName, err)
	}

	n.log.Info().
		Str("stream", streamName).
		Msg("Stream configuration updated successfully")

	// Log updated stream info
	updatedInfo := updatedStream.CachedInfo()
	n.log.Info().
		Str("stream", streamName).
		Uint64("messages", updatedInfo.State.Msgs).
		Uint64("bytes", updatedInfo.State.Bytes).
		Msg("Updated stream information")

	return nil
}
