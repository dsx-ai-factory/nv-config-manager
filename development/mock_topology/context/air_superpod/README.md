# DSX Air SuperPOD Mock Topology Context

This context is the source of truth for the DSX Air SuperPOD demo topology and the
Nautobot mock data loaded by the Design Builder mock topology job. The DSX Air sim
generates its temporary DSX Air topology YAML from these device JSON files, so there
is no separate maintained topology export for the built-in SuperPOD demo.

## Hyper-reach LLDP host

New SuperPOD simulations include `su02-hyperreach-host`, a dedicated Ubuntu LLDP
fixture, modeled in Nautobot as a child of the inventory-only
`su02-hyperreach-chassis` in DeviceBay `line-card-1`. The chassis is not an AIR
node. The Ubuntu child connects to `swp2` on both `su01-cin-leaf-r01` and
`su01-cin-leaf-r02`. The existing SpX compute hosts, their `ens3` addresses, and
their connections to `swp1` are unchanged. Keep the fixture's `swp2` ports out of
SpX tenant-change tests; continue using the existing compute hosts or spare
breakout ports for those tests.

| CIN leaf | Switch port | Intended host interface | Interface label | Advertised port ID |
| --- | --- | --- | --- | --- |
| su01-cin-leaf-r01 | swp2 | eth1 | C1-2-L1 | A56p0 |
| su01-cin-leaf-r02 | swp2 | eth2 | C1-2-L2 | A56p1 |

The host advertises `su02-hyperreach-chassis C1-2-L1` and
`su02-hyperreach-chassis C1-2-L2` as its LLDP port descriptions. Its actual data
NICs are named `A56p0` and `A56p1`, so their interface-name LLDP match intentionally
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

Run Connected Host Metadata against each CIN leaf. Its final table should show
the `swp2` intended interface label beside the observed LLDP port description
and `A56p0` or `A56p1` port ID. Then run Device Cable Validation for that leaf.
Both links should validate through the port-description path. For a negative
test, change one lane interactively, allow LLDP to refresh, and rerun validation:

```sh
sudo lldpcli configure ports A56p0 lldp portdescription \
  "su02-hyperreach-chassis WRONG-LANE"
```

The first link should fail identity validation while the other still passes.
The validation report should show intended label `C1-2-L1` beside observed
description `su02-hyperreach-chassis WRONG-LANE`, retaining `eth1` and `A56p0` as
the intended and actual port names. The CSV and Excel downloads include the
same metadata.

Restore the persistent fixture with `sudo systemctl restart lldpd`, wait for LLDP
to refresh, and rerun validation.

## Verify the chassis path

Create a new simulation from this context and run the updated Mock Network
Topology job with blueprint `air_superpod`. The job creates the chassis and child
device types, the chassis DeviceBay, and its installed child. Merely adding a
`parent_bay` field to JSON is insufficient: the DeviceBay must exist in Nautobot.
The loader installs the child after both devices exist and before cabling.

Verify in Nautobot that `su02-hyperreach-chassis` has bay `line-card-1` with
`su02-hyperreach-host` installed. The CIN-leaf `swp2` cables must still terminate
on the child's `eth1` and `eth2`, whose interface labels are `C1-2-L1` and
`C1-2-L2`. The child device name intentionally differs from the advertised chassis
name, so the `device_name` fallback cannot pass the port-description check.

For an existing simulation, update the mock topology job and its context files
and rerun it to create the bay association. Update both persistent port-description
lines in `/etc/lldpd.d/90-nvcm-demo.conf` on the Ubuntu fixture to use
`su02-hyperreach-chassis`, then restart `lldpd` and wait for neighbor refresh.
Recreating the simulation applies these changes automatically through cloud-init.

For a negative chassis test, advertise the child's name with the correct lane:

```sh
sudo lldpcli configure ports A56p0 lldp portdescription "su02-hyperreach-host C1-2-L1"
```

After neighbor refresh, `swp2` on `su01-cin-leaf-r01` must fail identity validation
while the second leaf still passes. Restart `lldpd` to restore the correct chassis
description and rerun validation; both should pass. Record the observed report
and cable status for each case.

To check the standalone fallback separately, temporarily clear the bay's installed
device in Nautobot and advertise the child's own name and lane. That description
should pass without `parent_bay`. Reinstall the child in `line-card-1` and restart
`lldpd` when finished. These checks cover the software's chassis and fallback paths;
AIR does not reproduce physical PCIe or cable electrical behavior.

## Drive validation through MCP

MCP cable-validation tools do not require physical devices. Use the operational
MCP server deployed inside the simulation, with the AIR tunnel and hostname
resolution available to the MCP client. The public documentation MCP server
cannot start workflows.

Discover the simulation's connection settings using its hostname:

```sh
uv run nvcm-mcp-cli config -H <sim-hostname> -k
```

Use `login` first when SSO is enabled. Configure an MCP-capable client with the
Streamable HTTP endpoint returned by discovery; keep tokens in the client's
secret storage. See the [remote MCP guide](../../../../docs/overview/mcp.mdx).
If discovery has no MCP endpoint, enable `mcp.enabled` in the simulation's Helm
values and redeploy the service through the normal installer process.

Perform the following calls through MCP, saving the workflow IDs and report
results as QA evidence:

1. List tools and confirm `query_nautobot`, `get_device_id`,
   `run_device_cable_validation`, and `get_workflow_detail` are present.
1. Use `query_nautobot` to verify the persisted chassis association:

   ```graphql
   query {
     devices(name: ["su02-hyperreach-host"]) {
       id
       name
       parent_bay {
         name
         device { name }
       }
     }
   }
   ```

   Expect `parent_bay.name = line-card-1` and
   `parent_bay.device.name = su02-hyperreach-chassis`. A missing bay fails this
   preflight; do not count a fallback-only run as chassis coverage.
1. Call `get_device_id` with `hostname` set to `su01-cin-leaf-r01`, then call
   `run_device_cable_validation` with the returned UUID as `device_id`. Leave
   `defer_cable_status_updates` and `ignore_no_neighbor` false.
1. Poll `get_workflow_detail` with the returned `workflow_id` until it completes.
   Inspect `swp2` in the report and confirm its intended label and observed chassis
   description match. A successful tool call alone is not a validation pass.
1. Repeat for `su01-cin-leaf-r02`, then repeat both calls for the wrong-lane and
   wrong-chassis cases above. An expected negative result can leave a failed stage
   waiting for manual retry; inspect that stage instead of waiting indefinitely
   for workflow completion. Restore the fixture and verify both links pass again.

These MCP calls exercise the normal workflow API, worker, and Nautobot persistence
path against the simulated devices. They still need to be run on the deployed
simulation; local unit tests do not establish live QA coverage.
