# Backbone sandbox workflow demonstrations

These workflows demonstrate the intended day-two workflow model before external
workflow plugins are available. They are branch-local examples registered like
the built-in workflows on the `bb-plugin-sandbox` branch.

The boundary is deliberate:

- Nautobot sandbox lookups and mutations are real.
- Jira validation and audit comments are real when a ticket is supplied.
- Interface drain uses the normal render, device diff, and guarded deployment
  activities. The local profile selects their mock device backend; Junos deployments
  select the NETCONF backend.
- Peering diffs come from consecutive Config Store renders. Device commits and
  post-deployment physical, IP, RTT, metric, and protocol observations remain
  explicitly mocked and labeled as such in stage output and Jira comments.

## Forms and parameter APIs

Both workflows have dedicated pages in the UI's New Workflow chooser:

- `/workflows/bbdraininterfaceworkflow/form`
- `/workflows/bbinternalbackbonebringupworkflow/form`

The forms use branch-local APIs under `/v1/parameter/bb-sandbox` for Backbone
devices, circuits, purpose-filtered interfaces, a common available LAG name,
and the next IPv4 or IPv6 point-to-point prefix. LAG suggestions use the same
`NautobotClient` allocator as workflow execution.

Prefix suggestions query only Nautobot prefixes matching the requested role,
namespace, and `type=container`, then inspect allocations within each matching
container. Assign the `BB-P2P` role to at least one IPv4 and one IPv6 container
in the Global namespace. The form requests the next `/31` and `/127` and submits
the returned explicit prefixes as workflow intent; the approved workflow writes
the resulting native prefix and address assignments into Nautobot.

For the local Kind environment, seed repeatable allocation pools and three
unprovisioned circuits through the gateway after the BB data population finishes:

```shell
NAUTOBOT_TOKEN=bb-sandbox-local-api-token uv run \
  development/bb_sandbox_workflows/seed_demo_data.py --insecure
```

The seeder also renders every Backbone router once in its populated Active state.
Those bootstrap renders are the pre-change Config Store revisions used by the drain
and bringup workflows; the workflows do not perform an artificial pre-change render.

## Interface drain

Input is a Nautobot device name or UUID, an interface/LAG name, and an optional
Jira issue. This workflow is the planning-before-persistence POC:

1. Resolves the real sandbox object and validates Jira when present.
2. Loads the renderer's current GraphQL data, copies it, and overrides only the
   interface status and `ISISInterface` values that execution would persist.
3. Calls the template renderer directly with that in-memory data. It neither changes
   Nautobot nor writes the proposed render to Config Store.
4. Calculates the candidate diff and records the plan on Jira when supplied, then
   waits for approval. The workflow can remain at this gate through CAB review while
   other operators continue changing Nautobot.
5. On approval, writes the proposed values to Nautobot, runs the normal render, and
   requires the persisted render to exactly match the approved in-memory render.
6. Rechecks the candidate diff against the device and applies it using the standard
   guarded deployment activity. A changed render or device diff stops execution and
   requires a new plan.

A rejection or empty diff leaves Nautobot, Config Store, and the device unchanged.

With `cluster.mock_devices: true`, the drain's device activity replaces the NETCONF
comparison with a compact Junos-style diff containing only the selected interface's
normal-to-Maintenance metric transition. Apply reconstructs and rechecks that exact
artifact, retaining the stale-approval guard without presenting a synthetic full-device
replacement to the operator.

## Internal Backbone bringup

Input is a circuit ID, Jira issue, both devices, both sets of physical members,
an optional common LAG name, an IPv4 `/31`, an IPv6 `/127`, expected RTT,
minimum-links, and an optional metric override. The circuit, devices, and ports
must already exist. When the LAG is omitted, the workflow selects the first
`aeN` at or above `ae100` unused on both devices and creates it on both sides.
The default metric is calculated once as `max(10, round(RTT ms * 10))`.
The workflow writes the selected value to Nautobot; templates never recalculate it.
Each Nautobot mutation triggers concurrent renders for router A and router Z. Their
actual Config Store revision diffs are pinned and approved independently, and the
two mock device commits can proceed in parallel. Each deploy is followed by a
separate validation stage so its approved diff and device-push result remain visible:

1. Physical deploy writes enabled state, LAG membership, and minimum-links, renders
   both routers, and waits for one approval per router. Validation checks member/LAG
   state and bidirectional LLDP observations.
2. Addressing deploy creates native Prefix, IP, and interface-assignment objects and
   stores expected RTT and Jira. Validation checks both applied address families
   before ping/RTT.
3. Routing deploy runs only after RTT passes and creates or updates the routing app's
   `ISISInterface` record for both LAGs. The presence of that explicit IS-IS
   intent—not a workflow phase flag—causes the
   templates to render IS-IS/MPLS/RSVP. Validation checks the applied metric and
   protocol health on both routers.

Any rejection makes later change stages unreachable. A final Jira comment records
every reached diff, reviewer, mocked RTT result, and the overall outcome.
