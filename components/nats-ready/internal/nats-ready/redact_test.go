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
	"strings"
	"testing"
)

func TestRedactAddress(t *testing.T) {
	tests := []struct {
		name    string
		address string
		want    string
	}{
		{"no credentials", "nats://nats:4222", "nats://nats:4222"},
		{"user and password", "nats://kiwi:s3cret@nats:4222", "nats://kiwi:xxxxx@nats:4222"},
		{"token only", "nats://s3cret@nats:4222", "nats://xxxxx@nats:4222"},
		{"websocket", "wss://kiwi:s3cret@nats.example.com", "wss://kiwi:xxxxx@nats.example.com"},
		{
			"server list",
			"nats://kiwi:s3cret@a:4222, nats://kiwi:s3cret@b:4222",
			"nats://kiwi:xxxxx@a:4222,nats://kiwi:xxxxx@b:4222",
		},
		{"unparseable", "nats://kiwi:s3cret@nats:bad port", "<unparseable address>"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := RedactAddress(tt.address)
			if got != tt.want {
				t.Errorf("RedactAddress(%q) = %q, want %q", tt.address, got, tt.want)
			}
			if strings.Contains(got, "s3cret") {
				t.Errorf("RedactAddress(%q) leaked the secret: %q", tt.address, got)
			}
		})
	}
}
