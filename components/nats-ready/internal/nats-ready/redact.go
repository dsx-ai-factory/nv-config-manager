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
	"net/url"
	"strings"
)

// RedactAddress returns a NATS server address that is safe to log. Passwords
// and tokens embedded in URL userinfo are masked. The address may be a
// comma-separated list of server URLs, as accepted by nats.Connect.
func RedactAddress(address string) string {
	servers := strings.Split(address, ",")
	for i, server := range servers {
		servers[i] = redactServer(strings.TrimSpace(server))
	}
	return strings.Join(servers, ",")
}

func redactServer(server string) string {
	u, err := url.Parse(server)
	if err != nil {
		// Unparseable input may still carry credentials; never echo it back.
		return "<unparseable address>"
	}
	if u.User == nil {
		return server
	}
	if _, hasPassword := u.User.Password(); hasPassword {
		return u.Redacted()
	}
	// A bare userinfo component is a token or nkey-style secret, not a username.
	u.User = url.User("xxxxx")
	return u.String()
}
