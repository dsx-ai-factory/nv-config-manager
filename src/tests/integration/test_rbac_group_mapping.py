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
"""Live-cluster tests for group-mapping RBAC (opt-in via --rbac).

These exercise the code paths that unit tests structurally cannot: the JWT
authenticator running the rbac sync during ``authenticate()`` against a real
Nautobot, including Nautobot's change-logging signals firing on
``ObjectPermission.delete()`` in the revoke path. That signal path is exactly
where the ``NoneType has no attribute 'pk'`` regression lived -- it needs a real
DB + a real change-context, so 93 green unit tests never caught it.

The suite assumes a ``make kind-up-sec`` deploy in the CONFIGURED state, i.e.
``nautobot.rbac.groupMapping`` set to the mapping in
``scripts/rbac-local-test/values-configured.yaml``:

    nvcm-network -> view: all, change: dcim.* + ipam.*
    nvcm-admin   -> is_superuser: true

and ``nautobot.rbac.autoCreateGroups: true`` so the managed Django Groups are
created on first login. See conftest ``rbac_*`` fixtures for the plumbing.

Run:
    uv run pytest src/tests/integration/test_rbac_group_mapping.py --rbac -v
"""

import json
import subprocess
import uuid
from collections.abc import Callable

import pytest

# Every test here drives several ``nbshell`` round-trips (kubectl exec + Django
# shell startup) plus a login/token mint, so under CI contention they can exceed
# the strict global 30s per-test timeout without actually hanging. Give this
# module more headroom; the rest of the suite keeps the tight default.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.rbac,
    pytest.mark.timeout(120),
    pytest.mark.dcim_provider("nautobot-2x"),
]

# Users seeded by scripts/create-local-security-nautobot-users, with the roles
# the local Keycloak realm assigns (password == username).
USER_NETWORK = "nvcm-network"  # roles: [nvcm-network]
USER_ADMIN = "nvcm-admin"  # roles: [nvcm-admin, nvcm-network]

_JSON_MARKER = "RBAC_JSON:"

# Identity / access-control models a ``content_types: ["all"]`` grant must never
# sweep in -- mirrors ``nv_config_manager_auth.rbac._PRIVILEGE_MODELS``. Granting
# these would be privilege escalation (mint API tokens, widen your own grants,
# flip ``is_superuser`` / rewrite group membership), so the "all" expansion
# excludes them. Kept in sync deliberately: this is the live-cluster assertion
# for that exclusion.
_PRIVILEGE_MODELS = frozenset(
    {
        "auth.group",
        "auth.permission",
        "contenttypes.contenttype",
        "users.user",
        "users.token",
        "users.objectpermission",
    }
)

# Suffix of the inert provenance marker attached to membership-only /
# ``is_superuser`` entries -- mirrors ``rbac._MEMBERSHIP_MARKER_SUFFIX``.
_MEMBERSHIP_MARKER_SUFFIX = "nvcm-managed-membership"


def _nbshell_json(nbshell: Callable[[str], str], body: str) -> dict:
    """Run *body* in the Django shell and return the dict it emits via ``_emit``.

    *body* must call ``_emit(<dict>)`` exactly once. We prepend a tiny ``_emit``
    helper that prints the result on a single ``RBAC_JSON:{...}`` line, then parse
    the last such line out of stdout (robust to any incidental output).
    """
    script = f"import json\ndef _emit(d): print('{_JSON_MARKER}' + json.dumps(d))\n{body}"
    out = nbshell(script)
    for line in reversed(out.splitlines()):
        line = line.strip()
        if line.startswith(_JSON_MARKER):
            return json.loads(line[len(_JSON_MARKER) :])
    raise AssertionError(f"shell did not emit a {_JSON_MARKER} line. Full output:\n{out}")


def _user_state(nbshell: Callable[[str], str], username: str) -> dict:
    """Return ``{exists, is_superuser, is_active, groups: [...]}`` for *username*."""
    body = (
        "from django.contrib.auth import get_user_model\n"
        "U = get_user_model()\n"
        f"u = U.objects.filter(username={username!r}).first()\n"
        "if u is None:\n"
        "    _emit({'exists': False})\n"
        "else:\n"
        "    _emit({\n"
        "        'exists': True,\n"
        "        'is_superuser': u.is_superuser,\n"
        "        'is_active': u.is_active,\n"
        "        'groups': sorted(u.groups.values_list('name', flat=True)),\n"
        "    })\n"
    )
    return _nbshell_json(nbshell, body)


def _managed_perms(nbshell: Callable[[str], str], group_name: str) -> list[str]:
    """Return the ``<group>_<action>`` ObjectPermission names for *group_name*."""
    body = (
        "from nautobot.users.models import ObjectPermission\n"
        f"names = list(ObjectPermission.objects.filter(name__startswith={group_name + '_'!r})"
        ".values_list('name', flat=True))\n"
        "_emit({'perms': sorted(names)})\n"
    )
    return _nbshell_json(nbshell, body)["perms"]


def _perm_grants(nbshell: Callable[[str], str], group_name: str) -> dict[str, dict]:
    """Return ``{name: {actions, object_types, groups}}`` for *group_name*'s managed perms.

    Asserting on names alone would let an empty-``actions`` or empty/mis-scoped
    ``object_types`` ObjectPermission pass the suite, so callers verify the
    concrete grant contents (which actions, over which content types).

    We also surface the ``groups`` M2M: an ObjectPermission only grants anything
    when it is attached to the Group (the name is just a label), so callers must
    confirm the perm is actually bound to *group_name* -- otherwise an orphaned
    perm (right name, attached to nothing) would pass while granting nothing.
    """
    body = (
        "from nautobot.users.models import ObjectPermission\n"
        f"qs = ObjectPermission.objects.filter(name__startswith={group_name + '_'!r})"
        ".prefetch_related('object_types', 'groups')\n"
        "out = {\n"
        "    p.name: {\n"
        "        'actions': sorted(p.actions),\n"
        "        'object_types': sorted(\n"
        "            f'{ct.app_label}.{ct.model}' for ct in p.object_types.all()\n"
        "        ),\n"
        "        'groups': sorted(p.groups.values_list('name', flat=True)),\n"
        "    }\n"
        "    for p in qs\n"
        "}\n"
        "_emit({'grants': out})\n"
    )
    return _nbshell_json(nbshell, body)["grants"]


@pytest.fixture(scope="session")
def rbac_require_configured(
    rbac_enabled: bool,
    config_manager_namespace: str,
    rbac_release: str,
) -> str:
    """Return the group-mapping ConfigMap name, failing if it isn't mounted.

    The suite is opt-in behind ``--rbac`` (skipped otherwise). Once you have
    opted in, a missing ConfigMap is a *setup error*, not a "not applicable"
    condition, so we ``fail`` rather than ``skip``: a skip reports green and
    would let a broken CONFIGURED deploy -- or a forgotten
    ``values-configured.yaml`` -- pass the whole suite silently, which defeats
    the point of running it.

    We also distinguish a genuinely-absent ConfigMap from a ``kubectl`` failure
    (cluster unreachable, RBAC-forbidden, wrong namespace). ``--ignore-not-found``
    makes "absent" a clean exit with empty output, so any non-zero return is a
    real tooling error and must never masquerade as "not configured".
    """
    if not rbac_enabled:
        pytest.skip("group-mapping RBAC tests require --rbac")
    configmap = f"{rbac_release}-nautobot-group-mapping"
    try:
        res = subprocess.run(
            [
                "kubectl",
                "get",
                "configmap",
                "-n",
                config_manager_namespace,
                configmap,
                "-o",
                "name",
                "--ignore-not-found",
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"kubectl timed out (30s) querying ConfigMap {configmap!r} in namespace "
            f"{config_manager_namespace!r}; the cluster / API server may be unreachable. "
            "Failing fast so this session-scoped fixture can't hang the suite."
        )
    if res.returncode != 0:
        pytest.fail(
            f"kubectl could not query ConfigMap {configmap!r} in namespace "
            f"{config_manager_namespace!r} (rc={res.returncode}): "
            f"{(res.stderr or res.stdout).strip()}"
        )
    if not res.stdout.strip():
        pytest.fail(
            f"ConfigMap {configmap!r} not found in namespace {config_manager_namespace!r}; "
            "deploy is not in the CONFIGURED state. Apply "
            "scripts/rbac-local-test/values-configured.yaml first "
            "(helm upgrade <release> deploy/helm --reuse-values -f <that file>)."
        )
    return configmap


def test_group_mapping_configmap_present(rbac_require_configured: str) -> None:
    """Sanity: the group-mapping ConfigMap is rendered and mounted."""
    assert rbac_require_configured.endswith("-nautobot-group-mapping")


def test_login_grants_managed_group_and_permissions(
    rbac_require_configured: str,
    rbac_api_login: Callable[[str], int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """A mapped user's login creates the managed Group + ObjectPermissions.

    ``nvcm-network`` carries the ``nvcm-network`` role, which the configured
    mapping grants ``view: all`` and ``change: dcim.* + ipam.*``. After login the
    user must belong to the ``nvcm-network`` Django Group and the
    ``nvcm-network_view`` / ``nvcm-network_change`` ObjectPermissions must exist
    (auto-created because ``autoCreateGroups: true``) with the correct actions
    AND non-empty, correctly scoped content types -- a name-only check would let
    an empty/mis-scoped grant through.
    """
    status = rbac_api_login(USER_NETWORK)
    assert status == 200, f"expected authorized 200 for {USER_NETWORK}, got {status}"

    state = _user_state(rbac_nbshell, USER_NETWORK)
    assert state["exists"], f"{USER_NETWORK} was not created by the JWT login"
    assert USER_NETWORK in state["groups"], (
        f"{USER_NETWORK} not added to managed group; groups={state['groups']}"
    )

    grants = _perm_grants(rbac_nbshell, USER_NETWORK)

    view = grants.get(f"{USER_NETWORK}_view")
    assert view is not None, f"missing view perm; got {sorted(grants)}"
    assert view["actions"] == ["view"], f"view perm has wrong actions: {view['actions']}"
    # Mapped to ``view: all`` -> must resolve to a non-empty content-type scope.
    assert view["object_types"], f"view perm ('all') resolved to an empty scope: {view}"
    # Must be bound to the managed group -- an orphaned perm grants nothing.
    assert USER_NETWORK in view["groups"], (
        f"view perm not attached to the {USER_NETWORK} group (orphaned): {view}"
    )

    change = grants.get(f"{USER_NETWORK}_change")
    assert change is not None, f"missing change perm; got {sorted(grants)}"
    assert change["actions"] == ["change"], f"change perm has wrong actions: {change['actions']}"
    # Mapped to ``change: dcim.* + ipam.*``: both app scopes must be present AND
    # nothing may leak beyond them. An ``any``-only check would let an
    # over-broad grant (e.g. an extra ``extras.*`` or a privilege model) pass, so
    # also assert every resolved content type is within dcim.*/ipam.*.
    change_ots = change["object_types"]
    assert any(o.startswith("dcim.") for o in change_ots), (
        f"change perm missing dcim.* scope: {change_ots}"
    )
    assert any(o.startswith("ipam.") for o in change_ots), (
        f"change perm missing ipam.* scope: {change_ots}"
    )
    leaked = [o for o in change_ots if not (o.startswith("dcim.") or o.startswith("ipam."))]
    assert not leaked, (
        f"change perm leaked scope beyond dcim.*/ipam.*: {leaked} (full scope: {change_ots})"
    )
    # Must be bound to the managed group -- an orphaned perm grants nothing.
    assert USER_NETWORK in change["groups"], (
        f"change perm not attached to the {USER_NETWORK} group (orphaned): {change}"
    )


def test_login_sets_superuser_from_mapping(
    rbac_require_configured: str,
    rbac_api_login: Callable[[str], int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """An ``is_superuser: true`` mapping entry promotes the user on login.

    ``make kind-up-sec`` seeds ``nvcm-admin`` as a superuser already (see
    scripts/create-local-security-nautobot-users), so a bare "assert is_superuser
    after login" would pass even if the group-mapping promotion path were broken.
    Demote the user first, then assert the login re-promotes it -- that isolates
    the assertion to our ``_sync_superuser_status`` code rather than the seed.
    """
    reset_body = (
        "from django.contrib.auth import get_user_model\n"
        "U = get_user_model()\n"
        f"user, _ = U.objects.get_or_create(username={USER_ADMIN!r})\n"
        "user.is_superuser = False\n"
        "user.is_staff = False\n"
        "user.save(update_fields=['is_superuser', 'is_staff'])\n"
        "_emit({'is_superuser': user.is_superuser, 'is_staff': user.is_staff})\n"
    )
    pre = _nbshell_json(rbac_nbshell, reset_body)
    assert not pre["is_superuser"], (
        f"precondition: {USER_ADMIN} should be demoted before login, got {pre}"
    )

    status = rbac_api_login(USER_ADMIN)
    assert status == 200, f"expected authorized 200 for {USER_ADMIN}, got {status}"

    state = _user_state(rbac_nbshell, USER_ADMIN)
    assert state["exists"], f"{USER_ADMIN} was not created by the JWT login"
    assert state["is_superuser"], (
        f"{USER_ADMIN} was not promoted to superuser by the mapping on login "
        f"(demoted pre-login, so this is our promotion path, not the seed): {state}"
    )


def test_revoke_prunes_stale_managed_group_without_crash(
    rbac_require_configured: str,
    rbac_api_login: Callable[[str], int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """Regression: the revoke path must delete stale managed perms, not crash.

    We plant a Django Group with a ``<group>_<action>`` ObjectPermission (the
    "previously managed" fingerprint) that is NOT in the current mapping, and add
    ``nvcm-network`` to it. On the next login, ``_revoke_removed_mapping_groups``
    must remove the membership and ``ObjectPermission.delete()`` the stale perm.

    That delete fires Nautobot's change-logging signal, which reads the acting
    user from the change context. Before the fix the sync ran with no bound user,
    so the signal hit ``AttributeError: 'NoneType' object has no attribute 'pk'``
    and -- being ``@transaction.atomic`` -- rolled the whole reconcile back
    (membership + perm survived; login errored). The fix wraps the sync in
    ``web_request_context(user)``. This asserts the post-fix outcome: login
    succeeds AND the stale membership/perm are gone.
    """
    stale_group = f"rbac-it-stale-{uuid.uuid4().hex[:8]}"
    stale_perm = f"{stale_group}_view"

    setup_body = (
        "from django.contrib.auth import get_user_model\n"
        "from django.contrib.auth.models import Group\n"
        "from nautobot.users.models import ObjectPermission\n"
        "U = get_user_model()\n"
        # Ensure the user exists even if this test runs before the grant test.
        f"user, _ = U.objects.get_or_create(username={USER_NETWORK!r})\n"
        f"group, _ = Group.objects.get_or_create(name={stale_group!r})\n"
        f"perm, _ = ObjectPermission.objects.get_or_create(name={stale_perm!r}, "
        "defaults={'actions': ['view']})\n"
        "perm.groups.add(group)\n"
        "user.groups.add(group)\n"
        "_emit({\n"
        "    'user_in_group': group.name in user.groups.values_list('name', flat=True),\n"
        f"    'perm_exists': ObjectPermission.objects.filter(name={stale_perm!r}).exists(),\n"
        "})\n"
    )
    try:
        pre = _nbshell_json(rbac_nbshell, setup_body)
        assert pre["user_in_group"], "precondition: user should be in the stale group"
        assert pre["perm_exists"], "precondition: stale perm should exist"

        # Login triggers the reconcile -> revoke path -> ObjectPermission.delete().
        status = rbac_api_login(USER_NETWORK)
        assert status == 200, (
            f"login regressed to {status}: the revoke path likely crashed on "
            "ObjectPermission.delete() (missing web_request_context user)."
        )

        state = _user_state(rbac_nbshell, USER_NETWORK)
        assert stale_group not in state["groups"], (
            f"stale group membership not revoked (rollback?): groups={state['groups']}"
        )
        remaining = _managed_perms(rbac_nbshell, stale_group)
        assert remaining == [], f"stale ObjectPermission not pruned (rollback?): {remaining}"
    finally:
        cleanup_body = (
            "from django.contrib.auth import get_user_model\n"
            "from django.contrib.auth.models import Group\n"
            "from nautobot.extras.context_managers import web_request_context\n"
            "from nautobot.users.models import ObjectPermission\n"
            # Delete inside a web_request_context bound to a real user: the
            # ObjectPermission.delete() here trips the same change-logging signal
            # the test exercises, so a context-less teardown could crash and mask
            # the real assertion failure.
            f"user = get_user_model().objects.get(username={USER_NETWORK!r})\n"
            "with web_request_context(user, context_detail='nv-config-manager-auth: RBAC integration cleanup'):\n"
            f"    ObjectPermission.objects.filter(name={stale_perm!r}).delete()\n"
            f"    Group.objects.filter(name={stale_group!r}).delete()\n"
            "_emit({'cleaned': True})\n"
        )
        _nbshell_json(rbac_nbshell, cleanup_body)


def test_all_scope_excludes_privilege_models(
    rbac_require_configured: str,
    rbac_api_login: Callable[..., int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """A ``content_types: ["all"]`` grant resolves to data models, never privilege ones.

    ``nvcm-network`` maps ``view`` to ``content_types: ["all"]``. The "all"
    expansion is deliberately narrowed (``rbac._EXCLUDED_FROM_ALL``): identity /
    access-control models -- ``users.user``, ``users.token``,
    ``users.objectpermission``, ``auth.group``, ``auth.permission``,
    ``contenttypes.contenttype`` -- are excluded so a broad ``all`` grant can't
    become a privilege-escalation backdoor around Nautobot's own object-type
    policy.

    Checked two ways: (1) the resolved ObjectPermission scope is non-empty (real
    data models present) yet contains none of the protected models; and (2)
    end-to-end, the user can list a data endpoint but is *forbidden* from the
    users endpoint -- proving the exclusion is enforced by Nautobot, not just
    bookkeeping.
    """
    status = rbac_api_login(USER_NETWORK)
    assert status == 200, f"expected authorized 200 for {USER_NETWORK}, got {status}"

    grants = _perm_grants(rbac_nbshell, USER_NETWORK)
    view = grants.get(f"{USER_NETWORK}_view")
    assert view is not None, f"missing view perm; got {sorted(grants)}"

    scope = set(view["object_types"])
    assert scope, f"view perm ('all') resolved to an empty scope: {view}"

    leaked = scope & _PRIVILEGE_MODELS
    assert not leaked, (
        f"'all' expansion leaked privilege models into {USER_NETWORK}_view: {sorted(leaked)}"
    )
    # Sanity: a real data model IS present, so the exclusion didn't nuke everything
    # (an empty scope would also technically "exclude" the privilege models).
    assert "dcim.device" in scope, (
        f"expected dcim.device in the 'all' scope; got a suspiciously narrow set: {sorted(scope)}"
    )

    # End-to-end: the exclusion must be *enforced*, not just recorded. Listing
    # users requires view on users.user, which "all" omits, so this must 403 --
    # while the data endpoint the default probe hit above returned 200.
    users_status = rbac_api_login(USER_NETWORK, "/api/users/users/")
    assert users_status == 403, (
        f"a 'view: all' grant must NOT authorize listing users (users.user is "
        f"excluded from the expansion); got {users_status} from /api/users/users/"
    )


def test_superuser_entry_creates_inert_membership_marker(
    rbac_require_configured: str,
    rbac_api_login: Callable[[str], int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """An ``is_superuser: true`` entry leaves an inert, revocable membership marker.

    ``nvcm-admin`` is membership-only (``is_superuser: true``, no per-action
    perms). Without a module-owned fingerprint on the group, a later removal of
    the entry could not be detected and the membership would dangle. So the sync
    attaches a single inert ``<group>_nvcm-managed-membership`` ObjectPermission:
    empty ``actions`` and empty ``object_types`` (grants nothing) but marks the
    group as ours. This asserts the marker exists and is genuinely inert; the
    end-to-end revoke it enables is covered by
    ``test_membership_only_group_revoked_via_marker``.
    """
    status = rbac_api_login(USER_ADMIN)
    assert status == 200, f"expected authorized 200 for {USER_ADMIN}, got {status}"

    grants = _perm_grants(rbac_nbshell, USER_ADMIN)
    marker_name = f"{USER_ADMIN}_{_MEMBERSHIP_MARKER_SUFFIX}"
    marker = grants.get(marker_name)
    assert marker is not None, (
        f"inert membership marker {marker_name!r} missing for the superuser entry; "
        f"got {sorted(grants)}"
    )
    assert marker["actions"] == [], f"marker must grant no actions: {marker}"
    assert marker["object_types"] == [], f"marker must have no object-type scope: {marker}"
    # The marker's whole purpose is to fingerprint the group as module-managed, so
    # it must be attached to that group; an orphaned marker exists by name but
    # can't drive the revoke path it was created to enable.
    assert USER_ADMIN in marker["groups"], (
        f"membership marker exists but is orphaned (not bound to the {USER_ADMIN} "
        f"group), so it can't mark the group as managed: {marker}"
    )
    # Superuser entries get ONLY the marker -- no per-action <group>_<action> perms.
    assert set(grants) == {marker_name}, (
        f"superuser group should carry only the inert marker; got {sorted(grants)}"
    )


def test_membership_only_group_revoked_via_marker(
    rbac_require_configured: str,
    rbac_api_login: Callable[[str], int],
    rbac_nbshell: Callable[[str], str],
) -> None:
    """A membership-only managed group is revoked on login via its marker alone.

    ``test_revoke_prunes_stale_managed_group_without_crash`` plants a
    ``<group>_<action>`` perm; this covers the membership-only fix. We plant a
    group whose ONLY module-owned fingerprint is the inert
    ``<group>_nvcm-managed-membership`` marker -- exactly what an ``is_superuser``
    or perms-less entry leaves behind -- and add ``nvcm-network`` to it. Because
    the group is not in the mapping, the next login must recognise it as managed
    *via the marker* and revoke the membership + prune the marker. Before the fix
    such a group had no ``<group>_<action>`` perm, so removal left the membership
    (and its access) dangling.
    """
    stale_group = f"rbac-it-marker-{uuid.uuid4().hex[:8]}"
    marker_perm = f"{stale_group}_{_MEMBERSHIP_MARKER_SUFFIX}"

    setup_body = (
        "from django.contrib.auth import get_user_model\n"
        "from django.contrib.auth.models import Group\n"
        "from nautobot.users.models import ObjectPermission\n"
        "U = get_user_model()\n"
        # Ensure the user exists even if this test runs before the grant test.
        f"user, _ = U.objects.get_or_create(username={USER_NETWORK!r})\n"
        f"group, _ = Group.objects.get_or_create(name={stale_group!r})\n"
        # Inert marker: empty actions + no object_types, exactly like the real one.
        f"perm, _ = ObjectPermission.objects.get_or_create(name={marker_perm!r}, "
        "defaults={'actions': [], 'constraints': {}})\n"
        "perm.object_types.set([])\n"
        "perm.groups.add(group)\n"
        "user.groups.add(group)\n"
        "_emit({\n"
        "    'user_in_group': group.name in user.groups.values_list('name', flat=True),\n"
        f"    'perm_exists': ObjectPermission.objects.filter(name={marker_perm!r}).exists(),\n"
        "})\n"
    )
    try:
        pre = _nbshell_json(rbac_nbshell, setup_body)
        assert pre["user_in_group"], "precondition: user should be in the marker-only group"
        assert pre["perm_exists"], "precondition: marker perm should exist"

        # Login triggers the reconcile -> revoke path for the removed group.
        status = rbac_api_login(USER_NETWORK)
        assert status == 200, (
            f"login regressed to {status}: the marker-driven revoke path likely crashed."
        )

        state = _user_state(rbac_nbshell, USER_NETWORK)
        assert stale_group not in state["groups"], (
            f"marker-only group membership not revoked: groups={state['groups']}"
        )
        remaining = _managed_perms(rbac_nbshell, stale_group)
        assert remaining == [], f"marker perm not pruned on revoke: {remaining}"
    finally:
        cleanup_body = (
            "from django.contrib.auth import get_user_model\n"
            "from django.contrib.auth.models import Group\n"
            "from nautobot.extras.context_managers import web_request_context\n"
            "from nautobot.users.models import ObjectPermission\n"
            f"user = get_user_model().objects.get(username={USER_NETWORK!r})\n"
            "with web_request_context(user, context_detail='nv-config-manager-auth: RBAC integration cleanup'):\n"
            f"    ObjectPermission.objects.filter(name={marker_perm!r}).delete()\n"
            f"    Group.objects.filter(name={stale_group!r}).delete()\n"
            "_emit({'cleaned': True})\n"
        )
        _nbshell_json(rbac_nbshell, cleanup_body)
