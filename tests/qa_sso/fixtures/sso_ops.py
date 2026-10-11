"""SSO-side QA operations, executed INSIDE the SSO server's Django shell (never imported by pytest).

    cd <sso-server repo>
    SSO_ALLOW_SYNTHETIC_SEED=1 QA_SSO_TESTS_DIR=<llm-wiki-svc>/tests/qa_sso \
      <python> manage.py shell -c "exec(open('<...>/fixtures/sso_ops.py').read()); seed()"

Functions: seed(), reset_user(u), grant(u, perm), revoke(u, perm), add_group(u, g), remove_group(u, g),
group_grant(g, perm), group_revoke(g, perm), set_active(u, bool), set_superuser(u, bool),
all_app_perms(), user_perms(u), delete_app_permission(codename).

SAFETY GUARD - same rule as sso-server qa/tests/fixtures/seed_test_data.py (deployment plan R27): refuses
(exit 2) unless SSO_ALLOW_SYNTHETIC_SEED=1 (alias QA_ALLOW_SEED=1) AND the database is local/throw-away
(SQLite :memory:/temp dir/a "qa" directory/name containing test or qa; MySQL host localhost/127.0.0.1/::1/
socket/db/mysql or SSO_SEED_LOCAL_HOSTS). Touches only users and groups whose names start with "qa_" and
the service client "qa-wiki-introspect".
"""
import json
import os
import sys
import tempfile
from pathlib import Path

from django.conf import settings as _settings


def _seed_guard() -> None:
    def refuse(msg: str) -> None:
        sys.stderr.write(f"REFUSING TO TOUCH THE SSO DB: {msg}\n"
                         "QA ops only run on a throw-away test DB (deployment plan R27). "
                         "See tests/qa_sso/fixtures/sso_ops.py.\n")
        raise SystemExit(2)

    flag = os.environ.get("SSO_ALLOW_SYNTHETIC_SEED") or os.environ.get("QA_ALLOW_SEED")
    if flag != "1":
        refuse("SSO_ALLOW_SYNTHETIC_SEED=1 is not set.")
    db = _settings.DATABASES["default"]
    engine, name = db.get("ENGINE", ""), str(db.get("NAME", ""))
    if engine.endswith("sqlite3"):
        if name == ":memory:" or name.startswith("file::memory:"):
            return
        path = Path(name).resolve()
        tmp = Path(tempfile.gettempdir()).resolve()
        lowered = path.name.lower()
        if (tmp in path.parents or "qa" in [p.lower() for p in path.parts[:-1]]
                or "qa_sso" in [p.lower() for p in path.parts[:-1]] or "test" in lowered or "qa" in lowered):
            return
        refuse(f"SQLite DB {path} is not under {tmp}, not under a 'qa' directory, and its name has no 'test'/'qa'.")
    host = str(db.get("HOST", "") or "")
    extra = {h.strip().lower() for h in os.environ.get("SSO_SEED_LOCAL_HOSTS", "").split(",") if h.strip()}
    if host.lower() in ({"", "localhost", "127.0.0.1", "::1", "db", "mysql"} | extra) or host.startswith("/"):
        return
    refuse(f"database host {host!r} (engine {engine}, name {name!r}) is not a local/container host.")


_seed_guard()
sys.path.insert(0, os.environ.get("QA_SSO_TESTS_DIR", "tests/qa_sso"))

from django.contrib.auth.hashers import make_password  # noqa: E402
from django.contrib.auth.models import Group, Permission, User  # noqa: E402
from django.core.management import call_command  # noqa: E402

import qasso_data as D  # noqa: E402


def _q(name: str) -> None:
    assert name.startswith("qa_"), f"refusing to touch non-QA row {name!r}"


def _perm(dotted: str) -> Permission:
    label, codename = dotted.split(".", 1)
    return Permission.objects.get(content_type__app_label=label, codename=codename)


def _ensure_app_perms() -> None:
    try:
        _perm(D.APP_PERM)
    except Permission.DoesNotExist:
        raise SystemExit(f"{D.APP_PERM} does not exist: is the SSO on branch feat/app-permissions and migrated?")
    try:
        _perm(D.OTHER_APP_PERM)
    except Permission.DoesNotExist:
        call_command("create_app_permission", D.OTHER_APP_CODENAME, "QA second application (llm-wiki QA)")


def reset_user(username: str) -> None:
    _q(username)
    u = D.USERS[username]
    user, _ = User.objects.update_or_create(username=username, defaults=dict(
        email=u["email"], first_name=u["first_name"], last_name=u["last_name"], is_staff=u["is_staff"],
        is_active=u["is_active"], is_superuser=u["is_superuser"], password=make_password(D.PASSWORD)))
    user.groups.set(Group.objects.filter(name__in=u["groups"]))
    user.user_permissions.set([_perm(p) for p in u["perms"]])
    try:
        from sso.models import UserTokenRevocation
        UserTokenRevocation.objects.filter(user=user).delete()
    except Exception as e:  # noqa: BLE001
        print("WARN: UserTokenRevocation reset skipped:", e)


def seed() -> None:
    _ensure_app_perms()
    for g, perms in D.GROUP_PERMS.items():
        _q(g)
        group, _ = Group.objects.get_or_create(name=g)
        group.permissions.set([_perm(p) for p in perms])
    for username in D.USERS:
        reset_user(username)
    from sso.models import ServiceClient
    for cid, secret, active, name in [
            (D.SERVICE_CLIENT_ID, D.SERVICE_CLIENT_SECRET, True, "qa llm-wiki introspect"),
            (D.LOGIN_CLIENT_ID, D.LOGIN_CLIENT_SECRET, True, "qa llm-wiki login (client_ip delegation)"),
            (D.INACTIVE_CLIENT_ID, D.INACTIVE_CLIENT_SECRET, False, "qa llm-wiki login, inactive")]:
        ServiceClient.objects.update_or_create(client_id=cid, defaults=dict(
            name=name, client_secret_hash=make_password(secret), is_active=active))
    print("QA_SSO_SEED_OK", len(D.USERS), "users")


def grant(username, perm):
    _q(username); User.objects.get(username=username).user_permissions.add(_perm(perm)); print("OK")


def revoke(username, perm):
    _q(username); User.objects.get(username=username).user_permissions.remove(_perm(perm)); print("OK")


def add_group(username, group):
    _q(username); _q(group); User.objects.get(username=username).groups.add(Group.objects.get(name=group)); print("OK")


def remove_group(username, group):
    _q(username); _q(group); User.objects.get(username=username).groups.remove(Group.objects.get(name=group)); print("OK")


def group_grant(group, perm):
    _q(group); Group.objects.get(name=group).permissions.add(_perm(perm)); print("OK")


def group_revoke(group, perm):
    _q(group); Group.objects.get(name=group).permissions.remove(_perm(perm)); print("OK")


def set_active(username, active):
    _q(username); User.objects.filter(username=username).update(is_active=bool(active)); print("OK")


def set_superuser(username, flag):
    _q(username); User.objects.filter(username=username).update(is_superuser=bool(flag)); print("OK")


def all_app_perms():
    print(json.dumps(sorted(f"{p.content_type.app_label}.{p.codename}"
                            for p in Permission.objects.filter(content_type__app_label="application"))))


def user_perms(username):
    _q(username)
    user = User.objects.get(username=username)
    print(json.dumps(sorted(p for p in user.get_all_permissions() if p.startswith("application."))))


def delete_app_permission(codename, allow_app1=False):
    """A3 scenario: simulate myFinance's remove_stale_contenttypes deleting an app permission row.
    Deleting app1 drops every app1 grant in the (throw-away) DB; callers must seed() again afterwards."""
    assert codename.startswith("qa_") or (allow_app1 and codename == D.APP_CODENAME), \
        "only QA app permissions (or app1 with allow_app1=True) may be deleted"
    Permission.objects.filter(content_type__app_label="application", codename=codename).delete()
    print("OK")
