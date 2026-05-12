#!/usr/bin/env python3
"""
Build a CSV of Superblocks Cloud org users with no matching user on Cloud Prem
(case-insensitive email), with an empty column for marking who to migrate.
Deactivated users (SCIM active=false) are omitted to match the users/groups skill.

Uses SCIM 2.0 GET /Users (paginated) on each deployment. Each host needs an organization
SCIM bearer token from Superblocks org settings — never the MCP server Bearer or CLI
auth.json session token. Tokens, base URLs, and auth-file paths are normally loaded from
the shared repository .env (see README.md "Shared .env (single source of config)").

Output path is fixed: migration-artifacts/cloud-prem-user-gap-selection.csv
(scim_lib.USER_GAP_CSV). The README and the SKILL refer to this path by name; the script
overwrites it on every run so the verify step can re-export and check the row count.

Examples:
  python3 scripts/export_cloud_prem_user_gap_csv.py

  python3 scripts/export_cloud_prem_user_gap_csv.py \\
    --cloud-base-url https://app.superblocks.com \\
    --cloud-prem-base-url https://acme.superblocks.com \\
    --cloud-token-file ~/.secrets/sb-cloud-scim-token.txt \\
    --cloud-prem-token-file ~/.secrets/sb-cloud-prem-scim-token.txt
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path
from typing import Any

from scim_lib import (
    SCIM_ENTERPRISE_USER_SCHEMA,
    USER_GAP_CSV,
    ScimHttpError,
    add_scim_dotenv_cli_args,
    apply_scim_dotenv_cli_args,
    load_mcp_remote_base,
    normalize_email,
    resolve_base_url,
    scim_list_all,
    user_primary_email,
)


def user_display_name(u: dict[str, Any]) -> str:
    dn = (u.get("displayName") or "").strip() if isinstance(u.get("displayName"), str) else ""
    if dn:
        return dn
    name = u.get("name") if isinstance(u.get("name"), dict) else None
    if name:
        given = (name.get("givenName") or "").strip()
        family = (name.get("familyName") or "").strip()
        joined = f"{given} {family}".strip()
        if joined and joined != ".":
            return joined
    return ""


def user_org_role(u: dict[str, Any]) -> str:
    ext = u.get(SCIM_ENTERPRISE_USER_SCHEMA)
    if isinstance(ext, dict):
        role = ext.get("role")
        if role:
            return str(role)
    role = u.get("role")
    return str(role) if role else ""


def user_is_active(u: dict[str, Any]) -> bool:
    active = u.get("active")
    if isinstance(active, bool):
        return active
    if isinstance(active, str):
        return active.strip().lower() in ("true", "1", "yes")
    # Default: treat missing `active` as active (matches SCIM convention).
    return True


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--cloud-base-url",
        dest="cloud_base_url",
        help="Cloud (SaaS) origin (default: $SUPERBLOCKS_CLOUD_BASE_URL from .env).",
    )
    ap.add_argument(
        "--cloud-prem-base-url",
        dest="cloud_prem_base_url",
        help="Cloud Prem origin (default: $SUPERBLOCKS_CLOUD_PREM_BASE_URL from .env).",
    )
    ap.add_argument(
        "--cloud-token-file",
        type=Path,
        help="Cloud SCIM bearer token file (overrides $SUPERBLOCKS_SCIM_TOKEN_CLOUD).",
    )
    ap.add_argument(
        "--cloud-prem-token-file",
        type=Path,
        dest="cloud_prem_token_file",
        help="Cloud Prem SCIM bearer token file (overrides $SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM).",
    )
    ap.add_argument(
        "--mcp-json",
        type=Path,
        default=Path.home() / ".cursor/mcp.json",
        help="Cursor MCP config used as a last-resort source for the Cloud Prem base URL.",
    )
    ap.add_argument(
        "--mcp-cloud-prem-key",
        default="superblocks-cloud-prem",
        dest="mcp_cloud_prem_key",
        help="mcpServers key for the Cloud Prem MCP server (when reading the URL from mcp.json).",
    )
    add_scim_dotenv_cli_args(ap)
    args = ap.parse_args()
    apply_scim_dotenv_cli_args(args)

    cloud_base = resolve_base_url(
        args.cloud_base_url,
        "SUPERBLOCKS_CLOUD_BASE_URL",
        label="Cloud (SaaS)",
    )
    cloud_prem_base = resolve_base_url(
        args.cloud_prem_base_url,
        "SUPERBLOCKS_CLOUD_PREM_BASE_URL",
        label="Cloud Prem",
        fallback=lambda: (
            load_mcp_remote_base(str(args.mcp_json), args.mcp_cloud_prem_key)
            if args.mcp_json and Path(args.mcp_json).is_file()
            else None
        ),
    )

    if args.cloud_token_file and args.cloud_token_file.is_file():
        cloud_token = args.cloud_token_file.read_text(encoding="utf-8").strip()
    else:
        cloud_token = (os.environ.get("SUPERBLOCKS_SCIM_TOKEN_CLOUD") or "").strip()
    if args.cloud_prem_token_file and args.cloud_prem_token_file.is_file():
        cloud_prem_token = args.cloud_prem_token_file.read_text(encoding="utf-8").strip()
    else:
        cloud_prem_token = (os.environ.get("SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM") or "").strip()

    if not cloud_token:
        raise SystemExit(
            "Missing Cloud SCIM bearer token. Set SUPERBLOCKS_SCIM_TOKEN_CLOUD in .env, or "
            "pass --cloud-token-file. See README.md \"SCIM access tokens (separate from MCP)\"."
        )
    if not cloud_prem_token:
        raise SystemExit(
            "Missing Cloud Prem SCIM bearer token. Set SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM in "
            ".env, or pass --cloud-prem-token-file. See README.md \"SCIM access tokens "
            "(separate from MCP)\"."
        )

    try:
        cloud_users = scim_list_all(cloud_base, cloud_token, "Users")
    except ScimHttpError as e:
        hint = (
            " Hint: use an Organization SCIM token (not MCP/CLI). See README.md."
            if e.code == 401
            else ""
        )
        raise SystemExit(
            f"SCIM list Users on Cloud failed ({cloud_base}): HTTP {e.code} {e.body!r}.{hint}"
        ) from e

    try:
        cloud_prem_users = scim_list_all(cloud_prem_base, cloud_prem_token, "Users")
    except ScimHttpError as e:
        hint = (
            " Hint: use an Organization SCIM token for Cloud Prem (not MCP). See README.md."
            if e.code == 401
            else ""
        )
        raise SystemExit(
            f"SCIM list Users on Cloud Prem failed ({cloud_prem_base}): HTTP {e.code} "
            f"{e.body!r}.{hint}"
        ) from e

    cloud_prem_emails = {
        normalize_email(user_primary_email(u))
        for u in cloud_prem_users
        if user_primary_email(u)
    }

    gap: list[dict[str, Any]] = []
    for u in cloud_users:
        if not user_is_active(u):
            continue
        email = user_primary_email(u)
        if not email:
            continue
        if normalize_email(email) in cloud_prem_emails:
            continue
        gap.append(
            {
                "name": user_display_name(u) or "",
                "email": email,
                "cloud_user_id": str(u.get("id") or ""),
                "cloud_org_role": user_org_role(u),
                "cloud_status": "active",
            }
        )

    gap.sort(key=lambda r: r["email"].lower())

    USER_GAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with USER_GAP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "migrate_to_cloud_prem",
                "name",
                "email",
                "cloud_user_id",
                "cloud_org_role",
                "cloud_status",
                "notes",
            ],
        )
        w.writeheader()
        for r in gap:
            w.writerow({**r, "migrate_to_cloud_prem": "", "notes": ""})

    print(
        f"Wrote {len(gap)} row(s) to {USER_GAP_CSV} "
        f"(Cloud {len(cloud_users)} users, Cloud Prem {len(cloud_prem_users)} users, "
        f"bases {cloud_base} -> {cloud_prem_base})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
