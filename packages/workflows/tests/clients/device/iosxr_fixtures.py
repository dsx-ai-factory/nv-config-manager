# SPDX-FileCopyrightText: Copyright (c) 2024-2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Synthetic IOS XR configuration used by the XR client tests.

SYNTHETIC: written by hand to exercise the parser and the diff. It is not a
capture from a router, and the secret value is a placeholder.
"""

HOST = "192.0.2.7"
USERNAME = "admin"

RUNNING = """\
Mon Oct 11 10:00:00.123 UTC
Building configuration...
!! IOS XR Configuration 26.4.1
!! Last configuration change at Mon Oct 11 09:00:00 2026 by admin
!
hostname xr-router-1
username admin
 group root-lr
 group cisco-support
 secret 10 $6$H0QFj2rx$placeholderplaceholder
!
aaa authentication login default local
ssh server v2
ssh server vrf MGMT
ssh server netconf vrf MGMT
netconf-yang agent
 ssh
!
line default
 transport input ssh
!
interface MgmtEth0/RP0/CPU0/0
 vrf MGMT
 ipv4 address 192.0.2.7 255.255.255.0
!
interface Loopback0
 description Underlay Loopback
 ipv4 address 10.0.0.7 255.255.255.255
!
interface HundredGigE0/0/0/0
 description WAN:XR-ROUTER-2:Hu0/0/0/1
 ipv4 address 10.1.1.0 255.255.255.254
!
router static
 vrf MGMT
  address-family ipv4 unicast
   0.0.0.0/0 192.0.2.1
  !
 !
!
router isis CORE
 net 49.0001.0100.0000.0007.00
 address-family ipv4 unicast
  metric-style wide
 !
 interface HundredGigE0/0/0/0
  point-to-point
  address-family ipv4 unicast
   metric 100
  !
 !
!
prefix-set PL-LOOPBACKS
  10.0.0.0/8 le 32,
  192.168.0.0/16
end-set
!
route-policy PASS
  pass
end-policy
!
end
"""

# Same content as RUNNING with a different layout: two-space indentation, no
# comments, no header lines. A converged router must produce an empty diff.
INTENDED = """\
hostname xr-router-1
username admin
  group root-lr
  group cisco-support
  secret 10 $6$H0QFj2rx$placeholderplaceholder
aaa authentication login default local
ssh server v2
ssh server vrf MGMT
ssh server netconf vrf MGMT
netconf-yang agent
 ssh
!
line default
  transport input ssh
interface MgmtEth0/RP0/CPU0/0
  vrf MGMT
  ipv4 address 192.0.2.7 255.255.255.0
interface Loopback0
  description Underlay Loopback
  ipv4 address 10.0.0.7 255.255.255.255
interface HundredGigE0/0/0/0
  description WAN:XR-ROUTER-2:Hu0/0/0/1
  ipv4 address 10.1.1.0 255.255.255.254
router static
  vrf MGMT
    address-family ipv4 unicast
      0.0.0.0/0 192.0.2.1
router isis CORE
  net 49.0001.0100.0000.0007.00
  address-family ipv4 unicast
    metric-style wide
  interface HundredGigE0/0/0/0
    point-to-point
    address-family ipv4 unicast
      metric 100
prefix-set PL-LOOPBACKS
  10.0.0.0/8 le 32,
  192.168.0.0/16
end-set
route-policy PASS
  pass
end-policy
"""
