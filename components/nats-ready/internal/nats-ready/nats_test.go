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
	"encoding/json"
	"reflect"
	"testing"
)

func embeddedDCIMStreamConfig(t *testing.T) StreamConfig {
	t.Helper()
	var streamConfig StreamConfig
	if err := json.Unmarshal(dcimConfigJSON, &streamConfig); err != nil {
		t.Fatalf("parse embedded DCIM stream config: %v", err)
	}
	return streamConfig
}

func TestApplyDCIMStreamOverridesDefaults(t *testing.T) {
	streamConfig := embeddedDCIMStreamConfig(t)

	applyDCIMStreamOverrides(&streamConfig)

	if streamConfig.Config.Name != "nautobot" {
		t.Fatalf("name = %q, want nautobot", streamConfig.Config.Name)
	}
	if !reflect.DeepEqual(streamConfig.Config.Subjects, []string{"nautobot"}) {
		t.Fatalf("subjects = %v, want [nautobot]", streamConfig.Config.Subjects)
	}
}

func TestApplyDCIMStreamOverridesLegacyEnv(t *testing.T) {
	t.Setenv("NAUTOBOT_NATS_STREAM_NAME", "legacy")
	t.Setenv("NAUTOBOT_NATS_STREAM_SUBJECTS", "legacy.a, legacy.b")
	streamConfig := embeddedDCIMStreamConfig(t)

	applyDCIMStreamOverrides(&streamConfig)

	if streamConfig.Config.Name != "legacy" {
		t.Fatalf("name = %q, want legacy", streamConfig.Config.Name)
	}
	if !reflect.DeepEqual(streamConfig.Config.Subjects, []string{"legacy.a", "legacy.b"}) {
		t.Fatalf("subjects = %v, want [legacy.a legacy.b]", streamConfig.Config.Subjects)
	}
}

func TestApplyDCIMStreamOverridesDCIMEnvWins(t *testing.T) {
	t.Setenv("NAUTOBOT_NATS_STREAM_NAME", "legacy")
	t.Setenv("NAUTOBOT_NATS_STREAM_SUBJECTS", "legacy")
	t.Setenv("DCIM_NATS_STREAM_NAME", "netbox")
	t.Setenv("DCIM_NATS_STREAM_SUBJECTS", "netbox.change")
	streamConfig := embeddedDCIMStreamConfig(t)

	applyDCIMStreamOverrides(&streamConfig)

	if streamConfig.Config.Name != "netbox" {
		t.Fatalf("name = %q, want netbox", streamConfig.Config.Name)
	}
	if !reflect.DeepEqual(streamConfig.Config.Subjects, []string{"netbox.change"}) {
		t.Fatalf("subjects = %v, want [netbox.change]", streamConfig.Config.Subjects)
	}
}
