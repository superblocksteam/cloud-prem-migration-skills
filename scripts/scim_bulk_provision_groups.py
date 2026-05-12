#!/usr/bin/env python3
"""
Create Superblocks org groups in bulk via SCIM 2.0 (POST /Groups).

Input CSV columns (header row required):
  display_name   — required; Superblocks group display name

Use after Cloud Prem user inventory and the group gap CSV from
export_cloud_prem_group_gap_csv.py, with a Cloud Prem SCIM token
(`SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM`; see README.md "SCIM access tokens (separate from MCP)").

Input path is fixed: migration-artifacts/cloud-prem-group-gap-selection.csv
(scim_lib.GROUP_GAP_CSV). Run scripts/export_cloud_prem_group_gap_csv.py to (re)generate it.

Examples:
  python3 scripts/scim_bulk_provision_groups.py --dry-run

  python3 scripts/scim_bulk_provision_groups.py --row-filter all
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
    GROUP_GAP_CSV,
    ScimHttpError,
    add_scim_dotenv_cli_args,
    apply_scim_dotenv_cli_args,
    csv_row_included_for_group_provision,
    load_base_url_cloud_prem_auth,
    load_mcp_remote_base,
    read_token,
    resolve_auth_file,
    resolve_base_url,
    run_with_backoff,
    scim_get_first_by_filter,
    scim_request,
)


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
        "all: every row with display_name in the CSV. Pass --row-filter all when you want "
        "to provision every group in the gap export without manually marking rows.",
    )
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-existing", action="store_true", default=True)
    ap.add_argument("--no-skip-existing", action="store_false", dest="skip_existing")
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
    if not GROUP_GAP_CSV.is_file():
        raise SystemExit(
            f"Missing input CSV: {GROUP_GAP_CSV}. "
            "Run scripts/export_cloud_prem_group_gap_csv.py first."
        )

    row_filter_mode = "all" if args.row_filter == "all" else "recommended"
    print(
        f"scim_bulk_provision_groups: --row-filter={row_filter_mode}, dry_run={args.dry_run}, "
        f"csv={GROUP_GAP_CSV}",
        file=sys.stderr,
    )

    ok = skip = err = excluded_by_row_filter = 0
    with GROUP_GAP_CSV.open(newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            row = {k or "": (v or "").strip() for k, v in raw.items()}
            if not csv_row_included_for_group_provision(row, row_filter_mode):
                excluded_by_row_filter += 1
                continue
            name = (row.get("display_name") or row.get("displayName") or "").strip()
            if not name:
                continue

            if args.skip_existing and not args.dry_run:
                existing = scim_get_first_by_filter(
                    base,
                    token,
                    "Groups",
                    f'displayName eq "{name}"',
                )
                if existing:
                    print(f"skip existing\t{name}", file=sys.stderr)
                    skip += 1
                    continue

            body: dict[str, Any] = {
                "schemas": ["urn:ietf:params:scim:schemas:core:2.0:Group"],
                "displayName": name,
            }
            if args.dry_run:
                print(f"would POST /Groups\t{name}", file=sys.stderr)
                ok += 1
                continue
            try:
                _, created = run_with_backoff(
                    partial(scim_request, base, token, "POST", "/Groups", body=body)
                )
            except ScimHttpError as e:
                print(f"error\t{name}\t{e.code}\t{e.body}", file=sys.stderr)
                err += 1
                continue
            gid = created.get("id") if isinstance(created, dict) else None
            print(f"created\t{name}\tid={gid}", file=sys.stderr)
            ok += 1

    print(
        f"summary ok={ok} skip={skip} err={err} excluded_by_row_filter={excluded_by_row_filter}",
        file=sys.stderr,
    )
    if (
        ok == 0
        and skip == 0
        and err == 0
        and excluded_by_row_filter > 0
        and row_filter_mode == "recommended"
    ):
        print(
            "\nNo rows were selected. With --row-filter=recommended, set migrate_to_cloud_prem to "
            "y/yes/true/1/x (or use --row-filter all).\n",
            file=sys.stdout,
        )


if __name__ == "__main__":
    main()
