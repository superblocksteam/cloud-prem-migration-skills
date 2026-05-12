#!/usr/bin/env python3
"""
Create Superblocks org users in bulk via SCIM 2.0 (POST /Users).

Typical flow with cloud-to-cloud-prem-users-groups-migration:
  1) Build user + group gap CSVs (scripts/export_cloud_prem_user_gap_csv.py and
     scripts/export_cloud_prem_group_gap_csv.py).
  2) Run this script against Cloud Prem with an Organization Admin SCIM token.

Auth: organization SCIM access token (Bearer) from Cloud Prem org settings — not the MCP
server Bearer or CLI auth.json token. Pass --token / --token-file or set
SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM (see README.md "SCIM access tokens (separate from MCP)").

Base URL: Cloud Prem origin. Default: $SUPERBLOCKS_CLOUD_PREM_BASE_URL from .env. Falls back
to --cloud-prem-auth superblocksBaseUrl, then the Cloud Prem MCP entry in ~/.cursor/mcp.json.

Docs (SCIM base URL and attributes):
  https://docs.superblocks.com/admin/org-administration/auth/scim/okta

Input path is fixed: migration-artifacts/cloud-prem-user-gap-selection.csv
(scim_lib.USER_GAP_CSV). Generate it first with
scripts/export_cloud_prem_user_gap_csv.py.

Examples:
  python3 scripts/scim_bulk_provision_users.py --dry-run

  python3 scripts/scim_bulk_provision_users.py --base-url https://acme.superblocks.com \\
    --token-file ~/.secrets/sb-scim-token.txt --row-filter all

Org roles: CSV cloud_org_role / org_role / role map to SCIM property "role" on schema
urn:ietf:params:scim:schemas:extension:enterprise:2.0:User (owner, admin, developer,
end_user, or a custom org role key). Use --default-org-role when rows omit a role.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from functools import partial
from pathlib import Path
from typing import Any

from scim_lib import (
    SCIM_CORE_USER_SCHEMA,
    SCIM_ENTERPRISE_USER_SCHEMA,
    USER_GAP_CSV,
    ScimHttpError,
    add_scim_dotenv_cli_args,
    apply_scim_dotenv_cli_args,
    cloud_org_role_to_scim_role,
    csv_row_included_for_provision,
    load_base_url_cloud_prem_auth,
    load_mcp_remote_base,
    read_token,
    resolve_auth_file,
    resolve_base_url,
    run_with_backoff,
    scim_get_first_by_filter,
    scim_request,
    split_display_name,
)


def build_user_body(
    email: str,
    display_name: str,
    *,
    active: bool = True,
    org_role: str | None = None,
) -> dict[str, Any]:
    """Build SCIM POST /Users JSON. Org role is the "role" field on enterprise:2.0:User (SCIM_ENTERPRISE_USER_SCHEMA)."""
    given, family = split_display_name(display_name or email)
    schemas = [SCIM_CORE_USER_SCHEMA]
    if org_role:
        schemas.append(SCIM_ENTERPRISE_USER_SCHEMA)
    body: dict[str, Any] = {
        "schemas": schemas,
        "userName": email,
        "active": active,
        "emails": [{"primary": True, "value": email, "type": "work"}],
        "displayName": (display_name or email).strip() or email,
        "name": {"givenName": given, "familyName": family},
    }
    if org_role:
        # Superblocks: org role SCIM attribute name is "role" on enterprise:2.0:User.
        body[SCIM_ENTERPRISE_USER_SCHEMA] = {"role": org_role}
    return body


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--cloud-prem-auth",
        type=str,
        dest="cloud_prem_auth",
        help="Path to Cloud Prem auth.json (uses superblocksBaseUrl only). "
        "Default: $SUPERBLOCKS_CLOUD_PREM_AUTH_FILE from .env.",
    )
    ap.add_argument(
        "--mcp-json",
        type=str,
        default=str(Path.home() / ".cursor/mcp.json"),
        help="Cursor MCP config used as a last-resort source for the Cloud Prem base URL.",
    )
    ap.add_argument(
        "--mcp-cloud-prem-key",
        default="superblocks-cloud-prem",
        dest="mcp_cloud_prem_key",
        help="mcpServers key for Cloud Prem MCP (when reading the URL from mcp.json).",
    )
    ap.add_argument(
        "--base-url",
        type=str,
        help="Cloud Prem origin override (default: $SUPERBLOCKS_CLOUD_PREM_BASE_URL from .env).",
    )
    ap.add_argument(
        "--token",
        type=str,
        help="Cloud Prem SCIM Bearer token (else SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM)",
    )
    ap.add_argument(
        "--token-file",
        type=str,
        help="File containing Cloud Prem SCIM token (overrides $SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM)",
    )
    ap.add_argument(
        "--row-filter",
        choices=("recommended", "all"),
        default="recommended",
        help="recommended: only rows with migrate_to_cloud_prem truthy (or recommended_action); "
        "all: every row with an email",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print actions without calling SCIM")
    ap.add_argument("--skip-existing", action="store_true", default=True, help="Skip if SCIM filter finds user")
    ap.add_argument("--no-skip-existing", action="store_false", dest="skip_existing")
    ap.add_argument(
        "--default-org-role",
        metavar="ROLE",
        help="When CSV has no usable org role, set SCIM role to this (e.g. developer, admin).",
    )
    add_scim_dotenv_cli_args(ap)
    args = ap.parse_args()
    apply_scim_dotenv_cli_args(args)

    cloud_prem_auth = resolve_auth_file(
        args.cloud_prem_auth, "SUPERBLOCKS_CLOUD_PREM_AUTH_FILE"
    )

    def _from_auth_or_mcp() -> str | None:
        if cloud_prem_auth and cloud_prem_auth.is_file():
            return load_base_url_cloud_prem_auth(str(cloud_prem_auth))
        if Path(args.mcp_json).is_file():
            return load_mcp_remote_base(args.mcp_json, args.mcp_cloud_prem_key)
        return None

    base = resolve_base_url(
        args.base_url,
        "SUPERBLOCKS_CLOUD_PREM_BASE_URL",
        label="Cloud Prem",
        fallback=_from_auth_or_mcp,
    )

    if args.dry_run:
        token = (args.token or "").strip()
        if not token and args.token_file:
            token = Path(args.token_file).read_text().strip()
        if not token:
            token = (os.environ.get("SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM") or "").strip()
    else:
        token = args.token or read_token(
            args.token_file,
            "SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM",
        )

    if not USER_GAP_CSV.is_file():
        raise SystemExit(
            f"Missing input CSV: {USER_GAP_CSV}. "
            "Run scripts/export_cloud_prem_user_gap_csv.py first."
        )

    default_scim_role: str | None = None
    if args.default_org_role:
        default_scim_role = cloud_org_role_to_scim_role(args.default_org_role)
        if not default_scim_role:
            raise SystemExit(
                f"Invalid --default-org-role {args.default_org_role!r}; "
                "use owner, admin, developer, end_user, or a custom org role key."
            )

    rows: list[dict[str, str]] = []
    with USER_GAP_CSV.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append({k or "": (v or "").strip() for k, v in row.items()})

    row_filter_mode = "all" if args.row_filter == "all" else "recommended"
    print(
        f"scim_bulk_provision_users: {len(rows)} data row(s) in {USER_GAP_CSV}, "
        f"--row-filter={row_filter_mode}, dry_run={args.dry_run}",
        file=sys.stderr,
    )

    ok = skip = err = excluded_by_row_filter = 0
    for row in rows:
        if not csv_row_included_for_provision(row, row_filter_mode):
            excluded_by_row_filter += 1
            continue
        email = (row.get("email") or "").strip()
        if not email:
            continue
        name = (row.get("name") or "").strip() or email
        role_raw = row.get("cloud_org_role") or row.get("org_role") or row.get("role")
        scim_role = cloud_org_role_to_scim_role(role_raw)
        if not scim_role and default_scim_role:
            scim_role = default_scim_role
        if not scim_role:
            print(
                f"warn\t{email}\tno org role in CSV; Superblocks will use the org default role "
                f"(set cloud_org_role or --default-org-role)",
                file=sys.stderr,
            )

        if args.skip_existing and not args.dry_run:
            existing = scim_get_first_by_filter(
                base,
                token,
                "Users",
                f'emails.value eq "{email}"',
            )
            if existing is None:
                existing = scim_get_first_by_filter(
                    base,
                    token,
                    "Users",
                    f'userName eq "{email}"',
                )
            if existing:
                print(f"skip existing\t{email}", file=sys.stderr)
                skip += 1
                continue

        body = build_user_body(email, name, org_role=scim_role)
        if args.dry_run:
            print(
                f"would POST /Users\t{email}\torg_role={scim_role}\tschemas={body.get('schemas')}",
                file=sys.stderr,
            )
            ok += 1
            continue
        try:
            _, created = run_with_backoff(
                partial(scim_request, base, token, "POST", "/Users", body=body)
            )
        except ScimHttpError as e:
            print(f"error\t{email}\t{e.code}\t{e.body}", file=sys.stderr)
            err += 1
            continue
        uid = created.get("id") if isinstance(created, dict) else None
        print(f"created\t{email}\tid={uid}", file=sys.stderr)
        ok += 1

    print(
        f"summary ok={ok} skip={skip} err={err} excluded_by_row_filter={excluded_by_row_filter}",
        file=sys.stderr,
    )
    if ok == 0 and skip == 0 and err == 0 and excluded_by_row_filter > 0 and row_filter_mode == "recommended":
        print(
            "\nNo rows were selected. Default --row-filter=recommended only processes rows where:\n"
            "  migrate_to_cloud_prem is y, yes, true, 1, or x  (or recommended_action=provision_on_cloud_prem),\n"
            "or use --row-filter all to process every CSV row that has an email.\n",
            file=sys.stdout,
        )


if __name__ == "__main__":
    main()
