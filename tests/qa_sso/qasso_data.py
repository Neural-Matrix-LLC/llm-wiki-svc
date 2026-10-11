"""Synthetic SSO users, groups and grants for the llm-wiki-svc SSO-login QA suite.

Only rows whose username starts with "qa_" and groups whose name starts with "qa_" are touched.
Shared by the seed script (run inside the SSO's Django shell) and by the tests.
"""
from __future__ import annotations

PASSWORD = "Qa-Wiki-Passw0rd!2026"
WRONG_PASSWORD = "definitely-not-the-password"

APP_PERM = "application.app1"          # design A1 / B1 SSO_REQUIRED_PERMISSION default
APP_LABEL, APP_CODENAME = APP_PERM.split(".")
OTHER_APP_CODENAME = "qa_app2"         # created with create_app_permission (design A1)
OTHER_APP_PERM = f"application.{OTHER_APP_CODENAME}"
NON_APP_PERM = "auth.view_user"        # a stock Django permission: must never appear in `perms` (A2)

APP_GROUP = "qa_app1_users"            # group holding application.app1
EMPTY_GROUP = "qa_wiki_readers"        # group without any application.* permission
MANY_GROUPS = [f"qa_bulk_group_{i:02d}_with_a_longish_name" for i in range(40)]

SERVICE_CLIENT_ID = "qa-wiki-introspect"
SERVICE_CLIENT_SECRET = "qa-wiki-introspect-secret-0123456789abcdef"
# v1.1 item 4: llm-wiki authenticates to /login and /refresh as a ServiceClient and sends client_ip
LOGIN_CLIENT_ID = "qa-wiki-login"
LOGIN_CLIENT_SECRET = "qa-wiki-login-secret-0123456789abcdef0123"
INACTIVE_CLIENT_ID = "qa-wiki-login-inactive"
INACTIVE_CLIENT_SECRET = "qa-wiki-login-inactive-secret-0123456789"

# username -> spec. perms = direct user permissions ("app_label.codename"); groups = group names.
USERS: dict[str, dict] = {
    "qa_wiki_direct":   {"email": "qa_wiki_direct@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_group":    {"email": "qa_wiki_group@example.invalid", "groups": [APP_GROUP]},
    "qa_wiki_both":     {"email": "qa_wiki_both@example.invalid", "perms": [APP_PERM], "groups": [APP_GROUP]},
    # superusers (Thomas, 2026-10-11: no automatic app access; SSO_SUPERUSER_ALL_APPS defaults to false)
    "qa_wiki_super":    {"email": "qa_wiki_super@example.invalid", "is_superuser": True, "is_staff": True},  # no grant
    "qa_wiki_super_app1": {"email": "qa_wiki_super_app1@example.invalid", "is_superuser": True, "perms": [APP_PERM]},
    "qa_wiki_super_group": {"email": "qa_wiki_super_group@example.invalid", "is_superuser": True,
                            "groups": [APP_GROUP]},
    "qa_wiki_none":     {"email": "qa_wiki_none@example.invalid", "groups": [EMPTY_GROUP]},
    "qa_wiki_staff":    {"email": "qa_wiki_staff@example.invalid", "is_staff": True},          # staff != permission
    "qa_wiki_nonapp":   {"email": "qa_wiki_nonapp@example.invalid", "perms": [NON_APP_PERM]},  # only a non-app perm
    "qa_wiki_mixed":    {"email": "qa_wiki_mixed@example.invalid", "perms": [NON_APP_PERM, OTHER_APP_PERM, APP_PERM]},
    "qa_wiki_app2":     {"email": "qa_wiki_app2@example.invalid", "perms": [OTHER_APP_PERM]},  # other app only
    "qa_wiki_inactive": {"email": "qa_wiki_inactive@example.invalid", "perms": [APP_PERM], "is_active": False},
    "qa_wiki_inactive_super": {"email": "qa_wiki_isuper@example.invalid", "is_superuser": True, "is_active": False},
    "qa_wiki_inactive_super_app1": {"email": "qa_wiki_isuper_app1@example.invalid", "is_superuser": True,
                                    "is_active": False, "perms": [APP_PERM], "groups": [APP_GROUP]},
    "qa_wiki_many":     {"email": "qa_wiki_many@example.invalid", "perms": [APP_PERM, OTHER_APP_PERM],
                         "groups": MANY_GROUPS, "first_name": "Many", "last_name": "Groups-And-Perms"},
    # mutable users: tests grant/revoke/deactivate them and reset them afterwards
    "qa_wiki_mut_grant":  {"email": "qa_wiki_mut_grant@example.invalid"},
    "qa_wiki_mut_revoke": {"email": "qa_wiki_mut_revoke@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_mut_group":  {"email": "qa_wiki_mut_group@example.invalid", "groups": [APP_GROUP]},
    "qa_wiki_mut_deact":  {"email": "qa_wiki_mut_deact@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_logoutall":  {"email": "qa_wiki_logoutall@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_refresh":    {"email": "qa_wiki_refresh@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_race":       {"email": "qa_wiki_race@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_logout":     {"email": "qa_wiki_logout@example.invalid", "perms": [APP_PERM]},
    # throttle phase only (per-username limit 5/min at the SSO)
    "qa_wiki_thr_user":   {"email": "qa_wiki_thr_user@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_thr_victim": {"email": "qa_wiki_thr_victim@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_thr_deleg":  {"email": "qa_wiki_thr_deleg@example.invalid", "perms": [APP_PERM]},
    # server-side session lifecycle (absolute lifetime, restart, prune, fixation)
    "qa_wiki_sess":       {"email": "qa_wiki_sess@example.invalid", "perms": [APP_PERM]},
    "qa_wiki_sess2":      {"email": "qa_wiki_sess2@example.invalid", "perms": [APP_PERM]},
}
for _name, _u in USERS.items():
    assert _name.startswith("qa_"), _name
    _u.setdefault("perms", [])
    _u.setdefault("groups", [])
    _u.setdefault("is_active", True)
    _u.setdefault("is_staff", False)
    _u.setdefault("is_superuser", False)
    _u.setdefault("first_name", "")
    _u.setdefault("last_name", "")

#: groups -> application/non-application permissions held by the group
GROUP_PERMS: dict[str, list[str]] = {APP_GROUP: [APP_PERM], EMPTY_GROUP: [NON_APP_PERM]}
for _g in MANY_GROUPS:
    GROUP_PERMS[_g] = []


def expected_perms(username: str, superuser_all_apps: bool = False) -> list[str]:
    """Design A2 + v1.1 item 8 + superuser decision (2026-10-11): sorted application.* perms from user + groups;
    always a list ([] when none); superusers get only their explicit grants by default
    (SSO_SUPERUSER_ALL_APPS=false), all application.* only with the opt-in flag true; inactive = [].
    With the flag true the real set is every application.* permission in the DB (tests compare with
    sso_admin all_app_perms); this returns the seeded ones."""
    u = USERS[username]
    if not u["is_active"]:
        return []
    if u["is_superuser"] and superuser_all_apps:
        return sorted({APP_PERM, OTHER_APP_PERM})  # every application.* permission the seed creates
    perms = set(u["perms"])
    for g in u["groups"]:
        perms |= set(GROUP_PERMS.get(g, []))
    return sorted(p for p in perms if p.startswith("application."))
