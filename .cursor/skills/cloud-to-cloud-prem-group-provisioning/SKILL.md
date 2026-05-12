---
name: cloud-to-cloud-prem-group-provisioning
description: >-
  Guides the user through the GROUP portion of Phase 1 of the Cloud → Cloud Prem migration only:
  export_cloud_prem_group_gap_csv.py (SCIM GET /Groups) → review CSV → scim_bulk_provision_groups.py
  (SCIM POST /Groups). Tokens, base URLs, and auth-file paths come from the shared repository .env
  (SUPERBLOCKS_CLOUD_BASE_URL, SUPERBLOCKS_CLOUD_PREM_BASE_URL, SUPERBLOCKS_SCIM_TOKEN_CLOUD,
  SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM). Does NOT use any MCP tools. Does NOT migrate users,
  memberships, or resource access — use the matching narrower skills (cloud-to-cloud-prem-user-
  provisioning, cloud-to-cloud-prem-group-membership-sync) or the master users-groups-migration
  skill for the full Phase 1 workflow.
disable-model-invocation: true
---

# Cloud → Cloud Prem group provisioning (Phase 1, groups only)

This skill walks the user through **just** the group portion of Phase 1: discovering which Cloud groups are missing on Cloud Prem, letting them pick which to migrate, and creating them via SCIM `POST /Groups`. Users and memberships are out of scope — see the related skills below for those.

The user-facing reference for these commands is **`README.md` → Phase 1, Steps 1.4–1.6**. Mirror that flow exactly. Always run `--dry-run` first, summarize what the script will do, and only run the real command after the user confirms.

## Prerequisites

1. The shared `.env` is populated with the four required values (`SUPERBLOCKS_CLOUD_BASE_URL`, `SUPERBLOCKS_CLOUD_PREM_BASE_URL`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM`). If any are missing, stop and have the user fill them in.
2. Group provisioning does **not** depend on users being migrated first, but the membership-sync skill that follows will only resolve users + groups that exist on Cloud Prem. If the customer's full sequence is users → groups → memberships, mention that.

## Step-by-step execution

### 1. Confirm `.env`

Quote back to the user which of the four required variables are present (do **not** print token values). If any are missing, stop and have them populate `.env`.

### 2. Export the group gap CSV

```bash
python3 scripts/export_cloud_prem_group_gap_csv.py
```

Reads SCIM `GET /Groups` on both Cloud and Cloud Prem and writes only rows present on Cloud but missing on Cloud Prem to `migration-artifacts/cloud-prem-group-gap-selection.csv` (the path is fixed; the script overwrites it on every run). The match is case-insensitive on `displayName`.

Read the result count from stderr (`Wrote N row(s) …`). If 0, tell the user no groups need provisioning and move to step 6 (verify). Otherwise summarize the groups by name so they can decide which to migrate.

### 3. Ask the user which path to take — ALL or SELECT

**This is a mandatory branching question. Do not skip it, and do not pick for the user.**

Surface the gap count from step 2, then present the two paths explicitly and ask which one to take:

> I found **N** Cloud groups that aren't on Cloud Prem yet. How do you want to migrate them?
>
> **A) Migrate ALL of them** — I'll run the provisioning script with `--row-filter all`, which sends a `POST /Groups` for every row in the gap CSV.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-group-gap-selection.csv`, put `y` in the `migrate_to_cloud_prem` column for the rows you want, save, and then I'll run with the default `--row-filter recommended` (which only sends rows you marked).
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer. Accept short forms ("A", "all", "everyone", "B", "select", "let me pick", "I'll curate"). If the user answers vaguely or asks a follow-up question, clarify and re-ask — **do not proceed until they have picked a path**.

Remember the chosen path for steps 4 and 5:

- **Path A (all):** the next two commands take `--row-filter all`.
- **Path B (select):** wait for the user to confirm they've finished marking `y` in the CSV, then the next two commands use the default (no `--row-filter` flag).

If two Cloud groups collide after normalization (case-insensitive same `displayName`), stop and ask the user which is canonical before proceeding — regardless of which path they chose.

### 4. Dry-run group provisioning

Run the command for the path the user chose in step 3:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_groups.py --dry-run --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_groups.py --dry-run
```

Surface the `summary` line and any `excluded_by_row_filter` count:

- **Path A:** `excluded_by_row_filter` should be `0`. If it isn't, something's wrong — stop and inspect.
- **Path B:** `excluded_by_row_filter` should equal the number of rows the user did **not** mark. If `ok=0` and `excluded_by_row_filter` is high, the user may not have saved the CSV — ask them to confirm and re-run the dry-run.

### 5. Provision groups for real

Drop `--dry-run` from the matching command:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_groups.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_groups.py
```

Surface the `created` / `skip existing` / `error` lines and the summary. Treat any `error` row as a blocker until the user has reviewed it.

### 6. Verify

Re-run the group gap export and confirm the CSV shrinks to header-only (or to rows the user intentionally skipped):

```bash
python3 scripts/export_cloud_prem_group_gap_csv.py
wc -l migration-artifacts/cloud-prem-group-gap-selection.csv   # 1 line = header only = success
```

Surface any remaining rows so the user can confirm they are expected.

## Guardrails specific to this skill

- **Never** call any MCP tool. All Cloud and Cloud Prem reads/writes go through the SCIM scripts.
- **Never** print or commit SCIM tokens. When showing `.env` content, redact token values.
- **Always** dry-run before any SCIM `POST /Groups`, surface the summary, and wait for the user to confirm before running for real.
- **Do not** edit the gap CSV on the user's behalf without explicit instruction — the CSV is their selection tool.
- **Do not** create groups the gap export did not surface. If the user asks for a one-off create, either add a row to the CSV first (and re-export to confirm it's still missing on Cloud Prem) or stop and direct them to the Admin UI.
- **No duplicate display names:** if a Cloud group name matches a Cloud Prem group (case-insensitive), do not provision again. The script's `skip existing` check handles this automatically; surface the count to the user.
- **Cloud Prem `group_id` UUIDs never match Cloud UUIDs.** If the user needs to track the Cloud → Cloud Prem id mapping for Phase 3 (resource permissions), tell them it can be reconstructed any time by re-running the gap export and joining on `displayName` — they do not need to capture it manually now.

## Capability boundary

This skill covers SCIM `POST /Groups` (and the supporting gap export) only. It does **not**:

- Provision users (see `cloud-to-cloud-prem-user-provisioning`).
- Apply group memberships (see `cloud-to-cloud-prem-group-membership-sync`).
- Grant resource access (that is Phase 3 — `cloud-to-cloud-prem-permissions-migration` — and uses MCP, not SCIM).
- Delete groups on Cloud Prem (out of scope for migration — handle via Admin UI).
- Migrate group descriptions, custom attributes, or external IdP linkages beyond `displayName`.

## Related

- **Full Phase 1 orchestrator:** [cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md) — users + groups + memberships in one workflow.
- **Users portion:** [cloud-to-cloud-prem-user-provisioning](../cloud-to-cloud-prem-user-provisioning/SKILL.md)
- **Memberships portion:** [cloud-to-cloud-prem-group-membership-sync](../cloud-to-cloud-prem-group-membership-sync/SKILL.md)
- **Next phase:** application YAML and integration remapping — [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)
- Setup, full command reference, and security: `README.md` at repository root (Phase 1 section).
