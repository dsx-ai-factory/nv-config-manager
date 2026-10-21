# DSX Air SuperPOD Mock Topology Context

This context is the source of truth for the DSX Air SuperPOD demo topology and the
Nautobot mock data loaded by the Design Builder mock topology job. The DSX Air sim
generates its temporary DSX Air topology YAML from these device JSON files, so there
is no separate maintained topology export for the built-in SuperPOD demo.

## Hyper-reach LLDP host

New SuperPOD simulations include `su02-hyperreach-host`, a dedicated Ubuntu LLDP
fixture. It connects to `swp2` on both `su01-cin-leaf-r01` and
`su01-cin-leaf-r02`. The existing SpX compute hosts, their `ens3` addresses, and
their connections to `swp1` are unchanged. Keep the fixture's `swp2` ports out of
SpX tenant-change tests; continue using the existing compute hosts or spare
breakout ports for those tests.

| CIN leaf | Switch port | Intended host interface | Interface label | Advertised port ID |
| --- | --- | --- | --- | --- |
| su01-cin-leaf-r01 | swp2 | eth1 | C1-2-L1 | A56p0 |
| su01-cin-leaf-r02 | swp2 | eth2 | C1-2-L2 | A56p1 |

The host advertises `su02-hyperreach-host C1-2-L1` and
`su02-hyperreach-host C1-2-L2` as its LLDP port descriptions. Its actual data NICs
are named `A56p0` and `A56p1`, so their interface-name LLDP match intentionally
differs from the Nautobot interface names. The intended interfaces have no MAC
addresses in Nautobot, preventing an FDB/ARP match from masking the description
check. AIR-only deterministic MACs identify the virtual NICs for boot setup.

The installer attaches cloud-init before the host starts. It brings up the NICs,
installs `lldpd`, and writes `/etc/lldpd.d/90-nvcm-demo.conf` before starting the
daemon. Unlike interactive `lldpcli configure` overrides, these settings survive
a daemon restart or reboot. A separate `eth0` exit interface provides DHCP and
package-download access; the two test interfaces have no IP addresses, gateways,
or tenant configuration. This host is for cable identity validation, not SpX
traffic tests.

The opt-in `_air.lldp` mapping in the host JSON associates intended interface
names with runtime `name` and `port_description` values. Other hosts receive no
LLDP cloud-init. Fixture hosts must use Ubuntu, have an `eth0` exit interface,
and have no static IP addresses. Interface labels are loaded into Nautobot by
the mock topology job. Existing simulations need to be recreated from the
updated topology to get the new host and links.

After first boot, use the host's AIR console:

```sh
cloud-init status --wait
sudo lldpcli show interfaces details
sudo lldpcli show interface ports A56p0 details
sudo lldpcli show interface ports A56p1 details
```

On each CIN leaf, verify the corresponding `swp2` LLDP neighbor has the expected
port description, then run Device Cable Validation for that leaf. Both links
should validate through the port-description path. For a negative test, change
one lane interactively, allow LLDP to refresh, and rerun validation:

```sh
sudo lldpcli configure ports A56p0 lldp portdescription "su02-hyperreach-host WRONG-LANE"
```

The first link should fail identity validation while the other still passes.
Restore the persistent fixture with `sudo systemctl restart lldpd`, wait for LLDP
to refresh, and rerun validation. This fixture covers port-description and label
matching; it does not model physical PCIe line cards or exercise Nautobot
`parent_bay` chassis resolution.
