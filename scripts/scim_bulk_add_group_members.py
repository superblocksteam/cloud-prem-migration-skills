#!/usr/bin/env python3
"""
Add users to Superblocks groups via SCIM PATCH /Groups/{id} (members).

Pairs with scripts/export_cloud_prem_group_membership_gap_csv.py — the gap CSV produced
there is the recommended input. Rows are processed in order; users must already exist
on Cloud Prem (run the user gap export + provisioning first).

Input CSV columns:
  migrate_to_cloud_prem — y/yes/true/1/x to include the row under default selection
                          (--row-filter recommended). Ignored under --row-filter all.
  group_display_name    — required unless group_id is set
  group_id              — optional; SCIM group id (skips display-name lookup)
  user_email            — required (alias: email)
  cloud_group_id        — informational; ignored at apply time
  cloud_user_id         — informational; ignored at apply time
  notes                 — informational; ignored at apply time

Auth: Cloud Prem SCIM token (`SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM` in .env, or
--token / --token-file).

Input path is fixed: migration-artifacts/cloud-prem-group-membership-gap-selection.csv
(scim_lib.GROUP_MEMBERSHIP_GAP_CSV). Run
scripts/export_cloud_prem_group_membership_gap_csv.py to (re)generate it.

Examples:
  # Apply only rows the user marked with `y`
  python3 scripts/scim_bulk_add_group_members.py --dry-run

  # Apply every row in the CSV regardless of marks
  python3 scripts/scim_bulk_add_group_members.py --row-filter all
"""

from __future__ import annotations

import argparse
import csv
import sys
from functools import partial
from pathlib import Path
from typing import Any

from scim_lib import (
    GROUP_MEMBERSHIP_GAP_CSV,
    ScimHttpError,
    add_scim_dotenv_cli_args,
    apply_scim_dotenv_cli_args,
    load_base_url_cloud_prem_auth,
    load_mcp_remote_base,
    normalize_email,
    read_token,
    resolve_auth_file,
    resolve_base_url,
    run_with_backoff,
    scim_list_all,
    scim_request,
    user_primary_email,
)


def build_user_id_by_email(base: str, token: str) -> dict[str, str]:
    """Map normalized email → Cloud Prem SCIM user id by listing all /Users."""
    cache: dict[str, str] = {}
    for u in scim_list_all(base, token, "Users"):
        if not u.get("id"):
            continue
        email = user_primary_email(u)
        if email:
            cache[normalize_email(email)] = str(u["id"])
    return cache


def build_group_id_by_display_name(base: str, token: str) -> dict[str, str]:
    """Map lowercased displayName → Cloud Prem SCIM group id by listing all /Groups."""
    cache: dict[str, str] = {}
    for g in scim_list_all(base, token, "Groups"):
        dn = (g.get("displayName") or "").strip()
        if dn and g.get("id"):
            cache[dn.lower()] = str(g["id"])
    return cache


def group_member_ids(base: str, token: str, group_id: str) -> set[str]:
    """Return SCIM user ids already in the group (GET full group)."""

    try:
        _, g = scim_request(base, token, "GET", f"/Groups/{group_id}")
    except ScimHttpError:
        return set()
    if not isinstance(g, dict):
        return set()
    members = g.get("members") or []
    if not isinstance(members, list):
        return set()
    out: set[str] = set()
    for m in members:
        if isinstance(m, dict) and m.get("value"):
            out.add(str(m["value"]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument(
        "--cloud-prem-auth",
        type=str,
        dest="cloud_prem_auth",
        help="Path to Cloud Prem auth.json (superblocksBaseUrl only). "
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
        help="recommended (default): only rows with migrate_to_cloud_prem truthy "
        "(or recommended_action=provision_on_cloud_prem); "
        "all: every row with both a user email and a group reference. Pass --row-filter all "
        "when you want to apply every row in the gap export without manually marking rows.",
    )
    ap.add_argument("--dry-run", action="store_true")
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

    token = args.token or read_token(
        args.token_file,
        "SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM",
    )
    if not GROUP_MEMBERSHIP_GAP_CSV.is_file():
        raise SystemExit(
            f"Missing input CSV: {GROUP_MEMBERSHIP_GAP_CSV}. "
            "Run scripts/export_cloud_prem_group_membership_gap_csv.py first."
        )

    row_filter_mode = "all" if args.row_filter == "all" else "recommended"
    print(
        f"scim_bulk_add_group_members: --row-filter={row_filter_mode}, dry_run={args.dry_run}, "
        f"csv={GROUP_MEMBERSHIP_GAP_CSV}",
        file=sys.stderr,
    )

    # Prefetch users + groups once. SCIM `filter=` query support varies across implementations
    # (Superblocks included), and per-row filter requests were the source of false "no group"
    # / "no user" errors. Listing everything matches what the export script does, so if the
    # row is in the gap CSV at all, it'll be in these caches.
    print("Loading Cloud Prem users + groups for lookup...", file=sys.stderr)
    user_id_by_email = build_user_id_by_email(base, token)
    group_id_by_name = build_group_id_by_display_name(base, token)
    print(
        f"  cached {len(user_id_by_email)} users, {len(group_id_by_name)} groups",
        file=sys.stderr,
    )

    # Cache "already a member" lookups per group to avoid one GET /Groups/{id} per row.
    group_members_cache: dict[str, set[str]] = {}

    def members_of(gid: str) -> set[str]:
        if gid not in group_members_cache:
            group_members_cache[gid] = group_member_ids(base, token, gid)
        return group_members_cache[gid]

    truthy = ("y", "yes", "true", "1", "x")

    ok = skip = err = excluded_by_row_filter = 0
    with GROUP_MEMBERSHIP_GAP_CSV.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            email = (row.get("user_email") or row.get("email") or "").strip()
            gid = (row.get("group_id") or "").strip()
            gname = (row.get("group_display_name") or row.get("group_name") or "").strip()
            if not email:
                continue
            if not gid and not gname:
                continue

            if row_filter_mode == "recommended":
                mig = (row.get("migrate_to_cloud_prem") or "").strip().lower()
                rec = (row.get("recommended_action") or "").strip().lower()
                if mig not in truthy and rec != "provision_on_cloud_prem":
                    excluded_by_row_filter += 1
                    continue

            if not gid:
                gid = group_id_by_name.get(gname.lower(), "")
            if not gid:
                print(
                    f"error\tno group on Cloud Prem\t{gname}\t"
                    "(group missing from /Groups listing — re-run "
                    "scripts/export_cloud_prem_group_membership_gap_csv.py if the group "
                    "was just created)",
                    file=sys.stderr,
                )
                err += 1
                continue

            uid = user_id_by_email.get(normalize_email(email), "")
            if not uid:
                print(
                    f"error\tno user on Cloud Prem\t{email}\t"
                    "(user missing from /Users listing — re-run the user gap export + "
                    "provisioning if the user was just created)",
                    file=sys.stderr,
                )
                err += 1
                continue

            existing_members = members_of(gid)
            if uid in existing_members:
                print(f"skip member\t{gname or gid}\t{email}", file=sys.stderr)
                skip += 1
                continue

            patch_body: dict[str, Any] = {
                "schemas": ["urn:ietf:params:scim:api:messages:2.0:PatchOp"],
                "Operations": [
                    {
                        "op": "add",
                        "path": "members",
                        "value": [{"value": uid, "display": email}],
                    }
                ],
            }
            if args.dry_run:
                print(f"would PATCH /Groups/{gid}\tadd\t{email}", file=sys.stderr)
                ok += 1
                continue
            try:
                run_with_backoff(
                    partial(scim_request, base, token, "PATCH", f"/Groups/{gid}", body=patch_body)
                )
            except ScimHttpError as e:
                print(f"error\t{gname or gid}\t{email}\t{e.code}\t{e.body}", file=sys.stderr)
                err += 1
                continue
            existing_members.add(uid)  # keep the cache consistent for later rows
            print(f"added\t{gname or gid}\t{email}", file=sys.stderr)
            ok += 1

    print(
        f"summary ok={ok} skip={skip} err={err} excluded_by_row_filter={excluded_by_row_filter}",
        file=sys.stderr,
    )
    if ok == 0 and skip == 0 and err == 0 and excluded_by_row_filter > 0 and row_filter_mode == "recommended":
        print(
            "\nNo rows were selected. Default --row-filter=recommended only processes rows where:\n"
            "  migrate_to_cloud_prem is y, yes, true, 1, or x  (or recommended_action=provision_on_cloud_prem),\n"
            "or use --row-filter all to process every CSV row that has both a user email and a group reference.\n",
            file=sys.stdout,
        )


if __name__ == "__main__":
    main()
