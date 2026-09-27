# InfiniBand PKey replay fixtures

These histories were captured before moving the IB/DCIM activities and UFM host
mixins from the service into `nv_config_manager_workflows`. They are compatibility
fixtures, not generated test output, and should change only when an intentional
Temporal command or payload contract change is reviewed.

The histories come from these successful workflow scenarios:

- `ib_pkey_creation.json`: explicit-PKey creation.
- `ib_pkey_member_add.json`: two-interface member addition.
- `ib_pkey_member_delete.json`: two-interface member deletion and cleanup.
- `ib_pkey_member_update.json`: automatically approved additions-only reconciliation.

`test_ib_pkey_replay_contract.py` replays each history against the current workflow
implementation. This protects workflow type names, activity names, command ordering,
patch behavior, and serialized activity payload compatibility during the package move.
