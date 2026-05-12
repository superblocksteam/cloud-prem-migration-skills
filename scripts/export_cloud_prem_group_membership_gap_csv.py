#!/usr/bin/env python3
"""
Build a CSV of Superblocks Cloud group memberships that are MISSING on Cloud Prem,
filtered to user/group pairs where BOTH endpoints already exist on Cloud Prem
(i.e. you've already migrated the users and groups but not yet wired up memberships).

Uses SCIM 2.0 on each deployment:
  - GET /Users  (paginated)        for user inventory + email normalization
  - GET /Groups (paginated)        for group inventory + display name match
  - GET /Groups/{id}               for full member lists (per matched group)

Each host needs an organization SCIM bearer token. Tokens and base URLs come from the
shared repository .env (SUPERBLOCKS_CLOUD_BASE_URL / SUPERBLOCKS_CLOUD_PREM_BASE_URL,
SUPERBLOCKS_SCIM_TOKEN_CLOUD / SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM).

What gets emitted:
  Only memberships where ALL of the following hold:
    1. The Cloud group has a Cloud Prem counterpart (case-insensitive displayName match).
    2. The Cloud member's email exists on Cloud Prem (case-insensitive email match).
    3. The user is NOT already a member of the corresponding Cloud Prem group.

  This pairs with scim_bulk_add_group_members.py, which honors migrate_to_cloud_prem
  rows by default (same selection pattern as the user and group bulk scripts).

Output path is fixed: migration-artifacts/cloud-prem-group-membership-gap-selection.csv
(scim_lib.GROUP_MEMBERSHIP_GAP_CSV). The README and SKILL refer to this path by name; the
script overwrites it on every run so the verify step can re-export and check row count.

Examples:
  python3 scripts/export_cloud_prem_group_membership_gap_csv.py

  python3 scripts/export_cloud_prem_group_membership_gap_csv.py \\
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
    GROUP_MEMBERSHIP_GAP_CSV,
    ScimHttpError,
    add_scim_dotenv_cli_args,
    apply_scim_dotenv_cli_args,
    load_mcp_remote_base,
    normalize_email,
    resolve_base_url,
    scim_list_all,
    scim_request,
    user_primary_email,
)


def group_display_name(g: dict[str, Any]) -> str:
    return str(g.get("displayName") or "").strip()


def fetch_group_members(base: str, token: str, group_id: str) -> list[str]:
    """Return the SCIM user-ids that are currently members of {group_id} (best effort)."""
    try:
        _, payload = scim_request(base, token, "GET", f"/Groups/{group_id}")
    except ScimHttpError:
        return []
    if not isinstance(payload, dict):
        return []
    members = payload.get("members")
    if not isinstance(members, list):
        return []
    out: list[str] = []
    for m in members:
        if isinstance(m, dict) and m.get("value"):
            out.append(str(m["value"]))
    return out


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

    # --- 1) List users + groups on both sides (basic fields). ---
    try:
        cloud_users = scim_list_all(cloud_base, cloud_token, "Users")
        cloud_groups = scim_list_all(cloud_base, cloud_token, "Groups")
    except ScimHttpError as e:
        raise SystemExit(f"SCIM list on Cloud failed ({cloud_base}): HTTP {e.code} {e.body!r}") from e
    try:
        cloud_prem_users = scim_list_all(cloud_prem_base, cloud_prem_token, "Users")
        cloud_prem_groups = scim_list_all(cloud_prem_base, cloud_prem_token, "Groups")
    except ScimHttpError as e:
        raise SystemExit(
            f"SCIM list on Cloud Prem failed ({cloud_prem_base}): HTTP {e.code} {e.body!r}"
        ) from e

    # --- 2) Build identity lookups. ---
    # Cloud:    user_id -> normalized email
    cloud_uid_to_email: dict[str, str] = {}
    for u in cloud_users:
        uid = u.get("id")
        email = user_primary_email(u)
        if uid and email:
            cloud_uid_to_email[str(uid)] = normalize_email(email)

    # Cloud Prem: email -> user_id   AND user_id -> email   (round-trip resolution)
    cp_email_to_uid: dict[str, str] = {}
    cp_uid_to_email: dict[str, str] = {}
    for u in cloud_prem_users:
        uid = u.get("id")
        email = user_primary_email(u)
        if uid and email:
            ne = normalize_email(email)
            cp_email_to_uid[ne] = str(uid)
            cp_uid_to_email[str(uid)] = ne

    # --- 3) Intersect groups by case-insensitive displayName. ---
    cp_group_by_dn: dict[str, dict[str, Any]] = {}
    for g in cloud_prem_groups:
        dn = group_display_name(g)
        if dn and g.get("id"):
            cp_group_by_dn[dn.lower()] = g

    # --- 4) For each matched group, fetch member lists on both sides and compute the delta. ---
    gap: list[dict[str, Any]] = []
    matched_groups = 0
    skipped_no_cp_group = 0
    for cloud_g in cloud_groups:
        dn = group_display_name(cloud_g)
        if not dn:
            continue
        cp_g = cp_group_by_dn.get(dn.lower())
        if not cp_g:
            skipped_no_cp_group += 1
            continue
        matched_groups += 1

        cloud_gid = str(cloud_g.get("id") or "")
        cp_gid = str(cp_g.get("id") or "")
        if not cloud_gid or not cp_gid:
            continue

        cloud_member_uids = fetch_group_members(cloud_base, cloud_token, cloud_gid)
        cp_member_uids = fetch_group_members(cloud_prem_base, cloud_prem_token, cp_gid)
        cp_member_emails = {
            cp_uid_to_email[uid] for uid in cp_member_uids if uid in cp_uid_to_email
        }

        for uid in cloud_member_uids:
            email = cloud_uid_to_email.get(uid)
            if not email:
                continue                # Cloud user has no email or wasn't in /Users list
            if email not in cp_email_to_uid:
                continue                # User not on Cloud Prem yet — migrate user first
            if email in cp_member_emails:
                continue                # Already a member on Cloud Prem
            gap.append(
                {
                    "group_display_name": dn,
                    "user_email": email,
                    "cloud_group_id": cloud_gid,
                    "cloud_user_id": uid,
                }
            )

    gap.sort(key=lambda r: (r["group_display_name"].lower(), r["user_email"]))

    GROUP_MEMBERSHIP_GAP_CSV.parent.mkdir(parents=True, exist_ok=True)
    with GROUP_MEMBERSHIP_GAP_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f,
            fieldnames=[
                "migrate_to_cloud_prem",
                "group_display_name",
                "user_email",
                "cloud_group_id",
                "cloud_user_id",
                "notes",
            ],
        )
        w.writeheader()
        for r in gap:
            w.writerow({**r, "migrate_to_cloud_prem": "", "notes": ""})

    print(
        f"Wrote {len(gap)} membership gap row(s) to {GROUP_MEMBERSHIP_GAP_CSV} "
        f"(Cloud {len(cloud_groups)} groups, Cloud Prem {len(cloud_prem_groups)} groups, "
        f"{matched_groups} matched by displayName, "
        f"{skipped_no_cp_group} Cloud groups skipped because there's no Cloud Prem counterpart; "
        f"bases {cloud_base} -> {cloud_prem_base})",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
