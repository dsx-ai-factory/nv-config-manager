# DSX Air SuperPOD Mock Topology Context

This context supplies the SuperPOD AIR topology and Nautobot inventory.

## Hyper-reach fixture

`su02-hyperreach-host` is an Ubuntu LLDP host installed in DeviceBay
`line-card-1` of `su02-hyperreach-chassis`. The chassis exists only in Nautobot;
it does not require another AIR VM. Existing SpX hosts and links are unchanged.

| CIN leaf | Port | Child interface | Label | LLDP port ID |
| --- | --- | --- | --- | --- |
| su01-cin-leaf-r01 | swp2 | eth1 | C1-2-L1 | A56p0 |
| su01-cin-leaf-r02 | swp2 | eth2 | C1-2-L2 | A56p1 |

The host advertises `su02-hyperreach-chassis <label>` in each LLDP port
description. The chassis name differs from the child's name, exercising
Nautobot `parent_bay` resolution. Different interface names and absent intended
MAC addresses prevent the other identity checks from masking this path.

Cloud-init configures the host's interface names and persistent LLDP settings.
Its separate `eth0` interface provides DHCP and package-download access.
The data interfaces have no IP addresses or tenant configuration.

## Updating simulations

New simulations use these fixtures automatically. The Mock Network Topology job
creates the chassis, child, and DeviceBay association.

Simulations without `su02-hyperreach-host` must be recreated. If the host already
exists, reload the updated mock topology and change its persistent LLDP
descriptions to use `su02-hyperreach-chassis`, then restart `lldpd`.

## MCP

Cable validation can run through the simulation's operational MCP server using
`run_device_cable_validation`. Use `get_workflow_detail` to inspect the result.
See the [MCP connection guide](../../../../docs/overview/mcp.mdx).
