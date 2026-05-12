---
name: cloud-to-cloud-prem-user-provisioning
description: >-
  Guides the user through the USER portion of Phase 1 of the Cloud → Cloud Prem migration only:
  export_cloud_prem_user_gap_csv.py (SCIM GET /Users) → review CSV → scim_bulk_provision_users.py
  (SCIM POST /Users). Tokens, base URLs, and auth-file paths come from the shared repository .env
  (SUPERBLOCKS_CLOUD_BASE_URL, SUPERBLOCKS_CLOUD_PREM_BASE_URL, SUPERBLOCKS_SCIM_TOKEN_CLOUD,
  SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM). Does NOT use any MCP tools. Does NOT migrate groups,
  memberships, or resource access — use the matching narrower skills (cloud-to-cloud-prem-group-
  provisioning, cloud-to-cloud-prem-group-membership-sync) or the master users-groups-migration
  skill for the full Phase 1 workflow.
---

# Cloud → Cloud Prem user provisioning (Phase 1, users only)

This skill walks the user through **just** the user portion of Phase 1: discovering which Cloud users are missing on Cloud Prem, letting them pick which to migrate, and creating them via SCIM `POST /Users`. Groups and memberships are out of scope — see the related skills below for those.

The user-facing reference for these commands is **`README.md` → Phase 1, Steps 1.1–1.3**. Mirror that flow exactly. Always run `--dry-run` first, summarize what the script will do, and only run the real command after the user confirms.

## Prerequisites

1. The shared `.env` is populated with the four required values (`SUPERBLOCKS_CLOUD_BASE_URL`, `SUPERBLOCKS_CLOUD_PREM_BASE_URL`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM`). If any are missing, stop and have the user fill them in.
2. **Custom org roles** (if any) must already exist on Cloud Prem before `scim_bulk_provision_users.py` references them — the SCIM enterprise `role` attribute is rejected otherwise. If the user has custom roles, confirm they exist on Cloud Prem before running.

## Step-by-step execution

### 1. Confirm `.env`

Quote back to the user which of the four required variables are present (do **not** print token values). If any are missing, stop and have them populate `.env`.

### 2. Export the user gap CSV

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
```

Reads SCIM `GET /Users` on both Cloud and Cloud Prem and writes only rows present on Cloud but missing on Cloud Prem to `migration-artifacts/cloud-prem-user-gap-selection.csv` (the path is fixed; the script overwrites it on every run). Deactivated Cloud users (`active=false`) are filtered out automatically.

Read the result count from stderr (`Wrote N row(s) …`). If 0, tell the user no users need provisioning and move to step 6 (verify). Otherwise summarize the rows (count, role distribution if relevant) so they can decide which to migrate.

### 3. Ask the user which path to take — ALL or SELECT

**This is a mandatory branching question. Do not skip it, and do not pick for the user.**

Surface the gap count from step 2, then present the two paths explicitly and ask which one to take:

> I found **N** active Cloud users that aren't on Cloud Prem yet. How do you want to migrate them?
>
> **A) Migrate ALL of them** — I'll run the provisioning script with `--row-filter all`, which sends a `POST /Users` for every row in the gap CSV.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-user-gap-selection.csv`, put `y` in the `migrate_to_cloud_prem` column for the rows you want, save, and then I'll run with the default `--row-filter recommended` (which only sends rows you marked).
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer. Accept short forms ("A", "all", "everyone", "B", "select", "let me pick", "I'll curate"). If the user answers vaguely or asks a follow-up question, clarify and re-ask — **do not proceed until they have picked a path**.

Remember the chosen path for steps 4 and 5:

- **Path A (all):** the next two commands take `--row-filter all`.
- **Path B (select):** wait for the user to confirm they've finished marking `y` in the CSV, then the next two commands use the default (no `--row-filter` flag).

If the user wants a mix (e.g. "migrate most of them, but exclude these three"), that's path B — explain that marking `y` on the keepers is the only way to express partial selection.

### 4. Dry-run user provisioning

Run the command for the path the user chose in step 3:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_users.py --dry-run --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_users.py --dry-run
```

Surface the `summary` line and any `warn` / `excluded_by_row_filter` counts:

- **Path A:** `excluded_by_row_filter` should be `0`. If it isn't, something's wrong — stop and inspect.
- **Path B:** `excluded_by_row_filter` should equal the number of rows the user did **not** mark. If `ok=0` and `excluded_by_row_filter` is high, the user may not have saved the CSV — ask them to confirm and re-run the dry-run.

If many rows say `no org role in CSV; …`, ask whether to pass `--default-org-role developer` (or another built-in: `admin`, `owner`, `end_user`, or a custom org role key).

### 5. Provision users for real

Drop `--dry-run` from the matching command:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_users.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_users.py
```

Surface the `created` / `skip existing` / `error` lines and the summary. Treat any `error` row as a blocker until the user has reviewed it.

### 6. Verify

Re-run the user gap export and confirm the CSV shrinks to header-only (or to rows the user intentionally skipped):

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
wc -l migration-artifacts/cloud-prem-user-gap-selection.csv   # 1 line = header only = success
```

Surface any remaining rows so the user can confirm they are expected.

## Guardrails specific to this skill

- **Never** call any MCP tool. All Cloud and Cloud Prem reads/writes go through the SCIM scripts.
- **Never** print or commit SCIM tokens. When showing `.env` content, redact token values.
- **Always** dry-run before any SCIM `POST /Users`, surface the summary, and wait for the user to confirm before running for real.
- **Do not** edit the gap CSV on the user's behalf without explicit instruction — the CSV is their selection tool.
- **Do not** create users the gap export did not surface. If the user asks for a one-off create, either add a row to the CSV first (and re-export to confirm it's still missing on Cloud Prem) or stop and direct them to the Admin UI / SSO JIT.
- **Active-only:** the export already filters out `active=false` Cloud users. Do not bypass that, and do not hand-edit a row that was filtered.
- **No duplicates:** if a Cloud user matches a Cloud Prem entry by case-insensitive email, do not provision again. The script's `skip existing` check handles this automatically; surface the count to the user.

## Capability boundary

This skill covers SCIM `POST /Users` (and the supporting gap export) only. It does **not**:

- Provision groups (see `cloud-to-cloud-prem-group-provisioning`).
- Apply group memberships (see `cloud-to-cloud-prem-group-membership-sync`).
- Grant resource access (that is Phase 3 — `cloud-to-cloud-prem-permissions-migration` — and uses MCP, not SCIM).
- Deactivate users on Cloud Prem (out of scope for migration — handle via Admin UI).
- Migrate non-SCIM identity attributes such as custom IdP claims, manager hierarchies, or login MFA settings.
- Migrate API keys, personal access tokens, or session tokens.

## Related

- **Full Phase 1 orchestrator:** [cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md) — users + groups + memberships in one workflow.
- **Groups portion:** [cloud-to-cloud-prem-group-provisioning](../cloud-to-cloud-prem-group-provisioning/SKILL.md)
- **Memberships portion:** [cloud-to-cloud-prem-group-membership-sync](../cloud-to-cloud-prem-group-membership-sync/SKILL.md)
- **Next phase:** application YAML and integration remapping — [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)
- Setup, full command reference, and security: `README.md` at repository root (Phase 1 section).
