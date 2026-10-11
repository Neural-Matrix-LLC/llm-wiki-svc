"""APPCMD-*: `create_app_permission`, the shared-table safety net and migration ownership (design A1/A3/A4)."""
from __future__ import annotations

import pytest

import jwt_forge
import qasso_data as D
from conftest import sso_login

pytestmark = [pytest.mark.needs_manage]


def test_APPCMD_01_app1_exists_after_migrate(sso_admin, seeded):
    assert D.APP_PERM in sso_admin.json("all_app_perms")


def test_APPCMD_02_create_is_idempotent(sso_admin, seeded):
    before = sso_admin.json("all_app_perms")
    for _ in range(2):
        p = sso_admin.manage("create_app_permission", "qa_app3", "QA third app")
        assert p.returncode == 0
    after = sso_admin.json("all_app_perms")
    assert after.count("application.qa_app3") == 1
    assert set(after) - set(before) <= {"application.qa_app3"}


@pytest.mark.parametrize("codename", ["App1", "1app", "a", "app-1", "app 1", "app1;drop", "_app", "x" * 64,
                                      "app1\n", "", "äpp"])
def test_APPCMD_03_rejects_bad_codenames(sso_admin, seeded, codename):
    """codename must match ^[a-z][a-z0-9_]{1,62}$ (2..63 chars)."""
    before = sso_admin.json("all_app_perms")
    p = sso_admin.manage("create_app_permission", codename, "bad", check=False)
    assert p.returncode != 0, f"accepted {codename!r}: {p.stdout}"
    assert sso_admin.json("all_app_perms") == before


@pytest.mark.parametrize("codename", ["ab", "a" + "b" * 62, "app_2", "z9"])
def test_APPCMD_04_accepts_boundary_codenames(sso_admin, seeded, codename):
    p = sso_admin.manage("create_app_permission", codename, "QA boundary", check=False)
    assert p.returncode == 0, p.stderr
    assert f"application.{codename}" in sso_admin.json("all_app_perms")


def test_APPCMD_05_new_app_perm_not_given_to_superusers_by_default(sso_admin, sso_http, seeded):
    """Default SSO_SUPERUSER_ALL_APPS=false (2026-10-11): a newly created app permission reaches no superuser
    automatically (with or without other grants)."""
    sso_admin.manage("create_app_permission", "qa_app4", "QA fourth app")
    for user, expected in (("qa_wiki_super", []), ("qa_wiki_super_app1", [D.APP_PERM])):
        r = sso_login(sso_http, user)
        assert r.status_code == 200
        assert jwt_forge.payload(r.json()["access_token"])["perms"] == expected, user


def test_APPCMD_05b_new_app_perm_reaches_superusers_with_flag_true(sso_admin, cfg, seeded):
    """Opt-in variant: on the SSO_SUPERUSER_ALL_APPS=true instance a new app permission reaches superusers
    without a code change."""
    if not cfg.sso_allapps_url:
        pytest.skip("SSO_ALLAPPS_URL not set")
    import httpx
    sso_admin.manage("create_app_permission", "qa_app4", "QA fourth app", check=False)
    with httpx.Client(base_url=cfg.sso_allapps_url, timeout=30) as c:
        r = sso_login(c, "qa_wiki_super")
    assert r.status_code == 200
    assert "application.qa_app4" in jwt_forge.payload(r.json()["access_token"])["perms"]


def test_APPCMD_06_command_created_permission_not_recreated(sso_admin, seeded, reset_user):
    """v1.1 item 8 (Q-13): post-migrate recreates only code-defined permissions; a create_app_permission row
    deleted by myFinance's remove_stale_contenttypes stays gone (documented) until the command is re-run."""
    reset_user("qa_wiki_app2")
    sso_admin.call("delete_app_permission", D.OTHER_APP_CODENAME)
    try:
        sso_admin.manage("migrate", "--noinput")
        assert D.OTHER_APP_PERM not in sso_admin.json("all_app_perms")
        assert D.APP_PERM in sso_admin.json("all_app_perms")
    finally:
        sso_admin.manage("create_app_permission", D.OTHER_APP_CODENAME, "QA second application (llm-wiki QA)")
        sso_admin.seed()


def test_APPCMD_10_app1_recreated_by_migrate_grants_lost(sso_admin, seeded):
    """A3: application.app1 (code-defined) deleted -> migrate recreates it; every app1 grant is lost."""
    sso_admin.call("delete_app_permission", D.APP_CODENAME, True)
    try:
        assert D.APP_PERM not in sso_admin.json("all_app_perms")
        sso_admin.manage("migrate", "--noinput")
        assert D.APP_PERM in sso_admin.json("all_app_perms")
        assert sso_admin.json("user_perms", "qa_wiki_direct") == [], "grants are expected to be lost"
    finally:
        sso_admin.seed()


def test_APPCMD_07_migration_owned_by_application_app(sso_admin):
    p = sso_admin.manage("showmigrations", "application")
    assert "[X]" in p.stdout and "0001" in p.stdout, p.stdout


def test_APPCMD_08_no_default_model_permissions(sso_admin, seeded):
    """default_permissions = () : no add/change/delete/view_applicationaccess noise."""
    perms = sso_admin.json("all_app_perms")
    assert not [p for p in perms if p.split(".")[1].endswith("_applicationaccess")], perms


def test_APPCMD_09_app_perm_table_has_no_rows(sso_admin, seeded):
    out = sso_admin.shell("from application.models import ApplicationAccess; print(ApplicationAccess.objects.count())")
    assert out.splitlines()[-1] == "0"
