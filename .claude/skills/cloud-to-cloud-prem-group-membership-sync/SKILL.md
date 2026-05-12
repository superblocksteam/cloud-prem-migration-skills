---
name: cloud-to-cloud-prem-group-membership-sync
description: >-
  Guides the user through the GROUP-MEMBERSHIP portion of Phase 1 of the Cloud → Cloud Prem
  migration only: export_cloud_prem_group_membership_gap_csv.py (intersect Cloud memberships
  with users + groups already on Cloud Prem) → review CSV → scim_bulk_add_group_members.py
  (SCIM PATCH /Groups/{id}). Cloud is the source of truth — the script never invents memberships.
  Tokens, base URLs, and auth-file paths come from the shared repository .env
  (SUPERBLOCKS_CLOUD_BASE_URL, SUPERBLOCKS_CLOUD_PREM_BASE_URL, SUPERBLOCKS_SCIM_TOKEN_CLOUD,
  SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM). Does NOT use any MCP tools. Does NOT migrate users
  or groups themselves — use the matching narrower skills (cloud-to-cloud-prem-user-provisioning,
  cloud-to-cloud-prem-group-provisioning) or the master users-groups-migration skill for the full
  Phase 1 workflow.
---

# Cloud → Cloud Prem group-membership sync (Phase 1, memberships only)

This skill walks the user through **just** the membership portion of Phase 1: discovering which Cloud memberships are missing on Cloud Prem (filtered to user/group pairs that already exist on Cloud Prem) and applying them via SCIM `PATCH /Groups/{id}`. User and group provisioning are out of scope — see the related skills below for those.

The user-facing reference for these commands is **`README.md` → Phase 1, Steps 1.7–1.9**. Mirror that flow exactly. Always run `--dry-run` first, summarize what the script will do, and only run the real command after the user confirms.

## Prerequisites

1. The shared `.env` is populated with the four required values (`SUPERBLOCKS_CLOUD_BASE_URL`, `SUPERBLOCKS_CLOUD_PREM_BASE_URL`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD`, `SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM`). If any are missing, stop and have the user fill them in.
2. **Users and groups must already exist on Cloud Prem** for the memberships you want to apply. The membership gap export filters out any pair where either side is missing on Cloud Prem and reports the skip count in stderr. If the user hasn't run user/group provisioning yet, direct them to [cloud-to-cloud-prem-user-provisioning](../cloud-to-cloud-prem-user-provisioning/SKILL.md) and [cloud-to-cloud-prem-group-provisioning](../cloud-to-cloud-prem-group-provisioning/SKILL.md) first.

## Step-by-step execution

### 1. Confirm `.env`

Quote back to the user which of the four required variables are present (do **not** print token values). If any are missing, stop and have them populate `.env`.

### 2. Export the group-membership gap CSV (Cloud is source of truth)

Cloud's group memberships are the source of truth — **do not** ask the user to author this CSV by hand. Run:

```bash
python3 scripts/export_cloud_prem_group_membership_gap_csv.py
```

Writes `migration-artifacts/cloud-prem-group-membership-gap-selection.csv` (the path is fixed; the script overwrites it on every run). The script emits **only** rows where (a) the group exists on Cloud Prem by display name, (b) the user exists on Cloud Prem by email, and (c) the user is not yet a member of the Cloud Prem group.

Surface the stderr summary — especially `N Cloud groups skipped because there's no Cloud Prem counterpart` — so the user knows whether they need to revisit group provisioning for any group before continuing.

If 0 rows are emitted, tell the user no memberships need applying and move to step 6 (verify).

### 3. Ask the user which path to take — ALL or SELECT

**This is a mandatory branching question. Do not skip it, and do not pick for the user.**

Surface the gap count from step 2, then present the two paths explicitly and ask which one to take:

> I found **N** memberships from Cloud that are missing on Cloud Prem (filtered to users + groups that already exist on Cloud Prem). How do you want to apply them?
>
> **A) Apply ALL of them** — I'll run the bulk-add script with `--row-filter all`, which sends a `PATCH /Groups/{id}` add for every row in the gap CSV.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-group-membership-gap-selection.csv`, put `y` in the `migrate_to_cloud_prem` column for the rows you want, save, and then I'll run with the default `--row-filter recommended` (which only sends rows you marked).
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer. Accept short forms ("A", "all", "everyone", "B", "select", "let me pick", "I'll curate"). If the user answers vaguely or asks a follow-up question, clarify and re-ask — **do not proceed until they have picked a path**.

Remember the chosen path for steps 4 and 5:

- **Path A (all):** the next two commands take `--row-filter all`.
- **Path B (select):** wait for the user to confirm they've finished marking `y` in the CSV, then the next two commands use the default (no `--row-filter` flag).

### 4. Dry-run membership application

Run the command for the path the user chose in step 3:

```bash
# Path A — all rows
python3 scripts/scim_bulk_add_group_members.py --dry-run --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_add_group_members.py --dry-run
```

The script lists Cloud Prem users + groups once at startup and resolves every email + display name from those listings (no per-row SCIM `filter=` queries — Superblocks SCIM filter support is inconsistent and was the source of false "no group" / "no user" errors before).

Surface the cache totals (`cached N users, M groups`), the `would PATCH /Groups/{id}` lines, and the `summary` line:

- **Path A:** `excluded_by_row_filter` should be `0`. If it isn't, something's wrong — stop and inspect.
- **Path B:** `excluded_by_row_filter` should equal the number of rows the user did **not** mark. If `ok=0` and `excluded_by_row_filter` is high, the user may not have saved the CSV — ask them to confirm and re-run the dry-run.

If errors include `no group on Cloud Prem` or `no user on Cloud Prem`, the CSV is stale — re-run the membership export from step 2 to refresh it. **Do not** edit the CSV by hand to insert IDs.

### 5. Apply memberships for real

Drop `--dry-run` from the matching command:

```bash
# Path A — all rows
python3 scripts/scim_bulk_add_group_members.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_add_group_members.py
```

Surface the `added` / `skip member` / `error` lines and the summary. Treat any `error` row as a blocker until the user has reviewed it.

### 6. Verify

Re-run the membership gap export and confirm the CSV shrinks to header-only (or to rows the user intentionally skipped):

```bash
python3 scripts/export_cloud_prem_group_membership_gap_csv.py
wc -l migration-artifacts/cloud-prem-group-membership-gap-selection.csv   # 1 line = header only = success
```

Surface any remaining rows so the user can confirm they are expected.

## Guardrails specific to this skill

- **Never** call any MCP tool. All Cloud and Cloud Prem reads/writes go through the SCIM scripts.
- **Never** print or commit SCIM tokens. When showing `.env` content, redact token values.
- **Always** dry-run before any SCIM `PATCH /Groups/{id}`, surface the summary, and wait for the user to confirm before running for real.
- **Cloud is source of truth — never invent memberships.** If a Cloud user is not in a Cloud group, the same pair will not be in the gap CSV. Do not hand-author rows; re-run the export instead.
- **Do not** edit the gap CSV on the user's behalf without explicit instruction — the CSV is their selection tool.
- **Stale CSVs:** if the bulk-add script reports `no group on Cloud Prem` or `no user on Cloud Prem`, that means the Cloud Prem-side user/group listing has changed since the export. Re-run the membership export to refresh the file; do not guess SCIM IDs.
- **Memberships do not block app migration.** If the user is in a hurry, mention that Phase 2 (app/integration migration) can proceed in parallel with this step.

## Capability boundary

This skill covers SCIM `PATCH /Groups/{id}` adds (and the supporting gap export) only. It does **not**:

- Provision users (see `cloud-to-cloud-prem-user-provisioning`).
- Provision groups (see `cloud-to-cloud-prem-group-provisioning`).
- **Remove** memberships from Cloud Prem groups (out of scope — the script only adds). If the customer needs to revoke memberships, direct them to the Admin UI or a custom SCIM `remove` script.
- Grant resource access (that is Phase 3 — `cloud-to-cloud-prem-permissions-migration` — and uses MCP, not SCIM).
- Migrate org-level roles (those are part of user provisioning via the SCIM enterprise `role` attribute).

## Related

- **Full Phase 1 orchestrator:** [cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md) — users + groups + memberships in one workflow.
- **Users portion (run first if not done):** [cloud-to-cloud-prem-user-provisioning](../cloud-to-cloud-prem-user-provisioning/SKILL.md)
- **Groups portion (run first if not done):** [cloud-to-cloud-prem-group-provisioning](../cloud-to-cloud-prem-group-provisioning/SKILL.md)
- **Next phase:** application YAML and integration remapping — [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)
- Setup, full command reference, and security: `README.md` at repository root (Phase 1 section).
