# Temporal workflow replay fixtures

The four PKey histories were captured before moving the IB/DCIM activities and
UFM host mixins from the service into `nv_config_manager_workflows`. The UFM-port
and IB-GUID histories capture the completed extraction boundary. All six are fixed
compatibility fixtures, not generated test output, and should change only when
an intentional Temporal command or payload contract change is reviewed.

The histories come from these successful workflow scenarios:

- `ib_pkey_creation.json`: explicit-PKey creation.
- `ib_pkey_member_add.json`: two-interface member addition.
- `ib_pkey_member_delete.json`: two-interface member deletion and cleanup.
- `ib_pkey_member_update.json`: automatically approved additions-only reconciliation.
- `infiniband_get_unhealthy_ports.json`: empty unhealthy-port result after resolving a
  UFM device and invoking `get_ib_ports`.
- `ib_port_guid_discovery.json`: one discovered interface GUID followed by a dry-run
  sync.

`test_ib_pkey.py` replays each history against the current workflow
implementation. This protects workflow type names, activity names, command ordering,
patch behavior, and serialized activity payload compatibility during the package move.
`test_infiniband_discovery.py` provides the equivalent replay and
serialized-argument coverage for UFM port retrieval and IB GUID discovery.

## Configuration-lifecycle fixtures

The six deployment histories were captured from successful local Temporal
workflow executions after the configuration-lifecycle activity boundary was
established. They are fixed compatibility fixtures and must not be regenerated
solely because a Python module path changes.

- `backup.json`: running and intended configuration loading, candidate
  diff, backup persistence, and Config Manager metadata recording.
- `config_diff.json`: read-only intended configuration diff.
- `deploy.json`: intended configuration loading, approved candidate
  application, and backup child-workflow scheduling.
- `switch_os_upgrade.json`: Render validation and the Cumulus ZTP/image
  polling path.
- `infiniband_mlnx_os_upgrade.json`: MLNX download, install, reload,
  verification, and cleanup commands.
- `nvlinkswitch_firmware_upgrade.json`: firmware comparison, render-target
  validation, ZTP polling, reboot, reboot polling, and final comparison.

`test_deployment.py` replays every history and separately asserts the
scheduled activity order and serialized arguments.

## Validation, password, and Redfish fixtures

The six device-operation histories capture successful device and site cable
validation, hardware validation, device and site password rotation, and
Redfish provisioning with authenticated and DPU operations. They are fixed
compatibility fixtures. `test_device_operations.py` replays them and
separately freezes their scheduled activity names and serialized arguments.

## Diagnostics fixtures

The two diagnostics histories capture a complete ticketed run
with command and tech-support attachments and the explicit ticketless branch.
`test_diagnostics.py` replays both histories and separately freezes
their activity names, serialized arguments, and branch-specific omissions.

## Inventory and Hello World fixtures

The connected-host, LLDP, InfiniBand cable-validation, and basic Hello World
histories were captured from commit `61756d9f` on `main`, before their activity
imports moved into `nv_config_manager_workflows`. Each history records a successful
path with deterministic mock activity results.

`test_inventory_and_validation.py` replays the three inventory and validation
histories and freezes their scheduled activity order and representative serialized
arguments. `test_hello_world.py` covers both basic and approval-based Hello World
histories.

## Orchestration fixtures

The five orchestration histories were captured from successful workflow executions
using `main` commit `61756d9f6ec5a00da5908ba5687451b79aa310f9`, before the activity
package extraction in this branch. They cover these successful paths:

- `batch_deploy.json`: approval, two device configuration applications, and two
  backup child workflows.
- `multi_deploy.json`: discovery and diff collection for three devices followed by
  one approved batch child workflow.
- `tenant_deploy.json`: partial tenant configuration validation and application
  followed by a backup child workflow.
- `reprovision.json`: pre-reprovision backup, ZTP execution and polling, and the
  post-reprovision backup.
- `site_backup.json`: site discovery followed by two backup child workflows.

`test_orchestration.py` replays these histories against the current
workflow definitions and freezes their scheduled activity and child-workflow order.

## SpX overlay replay fixtures

The four SpX histories were captured from `main` at commit `61756d9f`, before the
DCIM, render, and deploy activities used by these workflows moved into
`nv_config_manager_workflows`. They are fixed compatibility fixtures and should
change only after an intentional Temporal command or payload contract review.

- `spx_overlay_creation.json`: route distinguisher allocation and VRF provisioning.
- `spx_overlay_deletion.json`: deletion of three unused VRFs and the overlay.
- `spx_overlay_assignment.json`: device and interface VRF assignment followed by
  overlay reconciliation and stale association cleanup.
- `spx_overlay_tenant_change.json`: assignment child workflow, tenant render wait,
  and pinned tenant deployment child workflow.

`test_spx_overlay.py` replays all four histories against the current
workflow implementation and separately freezes their activity order, serialized
DCIM arguments, and child workflow inputs.
