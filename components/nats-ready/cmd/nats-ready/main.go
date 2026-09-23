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
package main

import (
	"flag"
	"os"

	"github.com/rs/zerolog"
	"github.com/rs/zerolog/log"

	natsready "github.com/nvidia/nv-config-manager/components/nats-ready/internal/nats-ready"
)

func main() {
	// Setup zerolog
	log.Logger = log.Output(zerolog.ConsoleWriter{Out: os.Stderr})

	logger := log.With().
		Str("component", "main").
		Logger()

	logger.Info().Msg("Beginning NATS init check")

	address := flag.String("address", "nats://localhost:4222", "NATS server address")
	flag.Parse()

	logger.Info().Str("address", natsready.RedactAddress(*address)).Msg("NATS server address")

	natsReady, err := natsready.NewRunner(&natsready.NatsReadyConfig{
		Address: *address,
	})
	if err != nil {
		logger.Fatal().Err(err).Msg("Failed to create NATS runnable")
	}

	if err := natsReady.Run(); err != nil {
		logger.Fatal().Err(err).Msg("NATS readiness check failed")
	}
	logger.Info().Msg("NATS init check completed successfully")
}
