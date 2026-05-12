---
name: cloud-to-cloud-prem-users-groups-migration
description: >-
  Guides the user through Phase 1 of the Cloud → Cloud Prem migration: aligning organization
  users, groups, and group memberships via the SCIM 2.0 scripts in scripts/. Uses
  export_cloud_prem_user_gap_csv.py (SCIM GET /Users) for user inventory,
  export_cloud_prem_group_gap_csv.py (SCIM GET /Groups) for group inventory,
  export_cloud_prem_group_membership_gap_csv.py for memberships gap (Cloud source of truth,
  filtered to migrated principals + groups), and scim_bulk_provision_users.py /
  scim_bulk_provision_groups.py / scim_bulk_add_group_members.py for SCIM creates and
  membership PATCHes. Tokens, base URLs, and auth-file paths come from the shared repository
  .env (SUPERBLOCKS_CLOUD_BASE_URL, SUPERBLOCKS_CLOUD_PREM_BASE_URL, SUPERBLOCKS_SCIM_TOKEN_CLOUD,
  SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM). This skill does NOT use any MCP tools — every
  Cloud and Cloud Prem read/write goes through the scripts. Does not grant resource access —
  that is the permissions skill after apps exist on Cloud Prem.
---

# Cloud → Cloud Prem users and groups migration (Phase 1)

This skill aligns **who exists in the organization** — users, groups, and group memberships — between **Superblocks Cloud** and **Cloud Prem**, entirely via the SCIM 2.0 scripts under `scripts/`. **No MCP tools are used in this phase.** Your job as the agent is to guide the user through running the scripts in the correct order, reviewing the CSVs they produce, and verifying the result.

Resource access (apps, integrations, folders, workflows, jobs, knowledge, etc.) is **not** in scope here — that is Phase 3 ([cloud-to-cloud-prem-permissions-migration](../cloud-to-cloud-prem-permissions-migration/SKILL.md)) and runs after Phase 2 ([cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)).

## Overall migration order

1. **This skill — Phase 1:** resolve or provision principals on Cloud Prem without duplicating identities.
2. **Phase 2:** migrate target applications and their integrations.
3. **Phase 3:** update permissions so migrated resources are shared with the principals who had access in Cloud.

## Required configuration (shared `.env`)

Every script auto-loads the repository `.env` (`./.env` then `<repo>/.env`). Confirm these variables are set **before** running anything:

| Variable | Used for |
|----------|----------|
| `SUPERBLOCKS_CLOUD_BASE_URL` | Origin for SCIM `GET /Users` and `GET /Groups` on Cloud (e.g. `https://app.superblocks.com`). |
| `SUPERBLOCKS_CLOUD_PREM_BASE_URL` | Origin for every SCIM call against Cloud Prem (gap exports + bulk scripts). |
| `SUPERBLOCKS_SCIM_TOKEN_CLOUD` | Organization SCIM token for Cloud (org settings — **not** the MCP Bearer, **not** the CLI `auth.json` session token). |
| `SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM` | Organization SCIM token for Cloud Prem. |

Both tokens are required for their host — there is no single-token fallback. If a script exits with `Missing … SCIM bearer token`, stop and have the user populate the relevant variable in `.env`.

## Non-negotiable: no duplicate users; no duplicate group names; no fabricated memberships

**Before** any SCIM `POST /Users`, `POST /Groups`, or membership PATCH, the gap exports below must run successfully. They are the agent's only authoritative source of what already exists on Cloud Prem.

1. **User inventory:** `python3 scripts/export_cloud_prem_user_gap_csv.py`. The script paginates SCIM `GET /Users` on **both** Cloud and Cloud Prem and writes only the rows present on Cloud but missing on Cloud Prem. Deactivated Cloud users (`active=false`) are filtered out automatically.
2. **Group inventory:** `python3 scripts/export_cloud_prem_group_gap_csv.py`. Same pattern with SCIM `GET /Groups`. Note: Cloud and Cloud Prem `group_id` UUIDs **never** match — record the mapping by display name after groups are created on Cloud Prem.
3. **Membership inventory:** `python3 scripts/export_cloud_prem_group_membership_gap_csv.py`. Reads memberships from Cloud (source of truth), intersects with users + groups that already exist on Cloud Prem, and emits only the pairs that need to be added. **Never** invent memberships — if Cloud doesn't have the user in the group, the user does not go in the Cloud Prem group either.

If a Cloud-side user matches a Cloud Prem entry (case-insensitive email) → already migrated; do not provision again. If a Cloud-side group display name matches a Cloud Prem group (case-insensitive) → already exists; record the Cloud `group_id` → Cloud Prem `group_id` mapping (by display name) for Phase 3.

If two Cloud records collide after normalization (two users with the same email under different IDs, two groups with the same display name), **stop and ask the user** which is canonical before proceeding.

**Prefer skipping over duplicating when uncertain.**

## Active-only user exports

Any user list derived from `export_cloud_prem_user_gap_csv.py` must **never** include deactivated users (`active=false` in SCIM). The script already filters them — do not bypass that, and do not hand-edit a row that was filtered. The CSV columns are: `migrate_to_cloud_prem`, `name`, `email`, `cloud_user_id`, `cloud_org_role` (from the SCIM enterprise extension), `cloud_status` (always `active` because deactivated users are excluded), `notes`.

## Step-by-step execution

The user-facing reference for these commands is the repository **`README.md` → Phase 1**. Mirror that flow exactly. Always run a `--dry-run` first when one is available, summarize what the script will do, and only run the real command after the user confirms.

### 1. Confirm `.env`

Quote back to the user which of the four required variables are present (do not print token values). If any are missing, stop and have them fill in `.env`.

All Phase 1 scripts write/read **fixed paths** under `migration-artifacts/`. They are not user-overridable; each export overwrites its file and each bulk script reads exactly that file:

- `migration-artifacts/cloud-prem-user-gap-selection.csv` — produced by `export_cloud_prem_user_gap_csv.py`, consumed by `scim_bulk_provision_users.py`.
- `migration-artifacts/cloud-prem-group-gap-selection.csv` — produced by `export_cloud_prem_group_gap_csv.py`, consumed by `scim_bulk_provision_groups.py`.
- `migration-artifacts/cloud-prem-group-membership-gap-selection.csv` — produced by `export_cloud_prem_group_membership_gap_csv.py`, consumed by `scim_bulk_add_group_members.py`.

### 2. User gap CSV

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
```

Read the result count from stderr (`Wrote N row(s) …`). If 0, tell the user no users need provisioning and move on to groups. Otherwise summarize the rows (count, role distribution if relevant) so they can decide which to migrate.

### 3. Ask the user which path to take for USERS — ALL or SELECT

**Mandatory branching question. Do not skip it, and do not pick for the user.** Phase 1 has three of these branching questions (users now, groups in step 6, memberships in step 8) — each one is asked independently because the user may want different choices per artifact.

Surface the gap count from step 2 and ask:

> I found **N** active Cloud users that aren't on Cloud Prem yet. How do you want to migrate them?
>
> **A) Migrate ALL of them** — `--row-filter all`, sends a `POST /Users` for every row.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-user-gap-selection.csv`, put `y` in the `migrate_to_cloud_prem` column for the rows you want, save, and then I'll run with the default `--row-filter recommended`.
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer. Accept short forms ("A", "all", "B", "select", "let me curate"). If the user answers vaguely, clarify and re-ask — **do not proceed until they have picked a path**.

Remember the chosen path for steps 4 and 5.

### 4. Dry-run user provisioning

Run the command for the path the user chose in step 3:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_users.py --dry-run --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_users.py --dry-run
```

Surface the `summary` line and any `warn` / `excluded_by_row_filter` counts. Under path B, `excluded_by_row_filter` should equal the number of rows the user did **not** mark — if `ok=0` and `excluded_by_row_filter` is high, ask the user to confirm they saved the CSV. If many rows say `no org role in CSV; …`, ask whether to pass `--default-org-role developer` (or another built-in: `admin`, `owner`, `end_user`, or a custom org role key).

### 5. Provision users for real

Drop `--dry-run` from the matching command. Surface the `created` / `skip existing` / `error` lines and the summary. Treat any `error` row as a blocker until the user has reviewed it.

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_users.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_users.py
```

### 6. Group gap CSV + ask path for GROUPS — ALL or SELECT

```bash
python3 scripts/export_cloud_prem_group_gap_csv.py
```

If 0 rows, tell the user no groups need provisioning and move to memberships (step 8). Otherwise, **ask the same branching question again** — independent of what the user chose for users in step 3:

> I found **M** Cloud groups that aren't on Cloud Prem yet. How do you want to migrate them?
>
> **A) Migrate ALL of them** — `--row-filter all`, sends a `POST /Groups` for every row.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-group-gap-selection.csv`, mark `y` on the rows you want, save, and I'll run with the default `--row-filter recommended`.
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer. If two Cloud groups collide on case-insensitive `displayName`, stop and ask which is canonical before continuing (regardless of which path they chose).

### 7. Provision groups (dry-run, then real)

Run the dry-run **and** the real run for the path the user chose in step 6:

```bash
# Path A — all rows
python3 scripts/scim_bulk_provision_groups.py --dry-run --row-filter all
python3 scripts/scim_bulk_provision_groups.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_provision_groups.py --dry-run
python3 scripts/scim_bulk_provision_groups.py
```

Surface the dry-run summary. If `excluded_by_row_filter` is high and `ok=0` under path B, the user has not marked any rows — ask them to mark `y` or switch to path A. The script skips groups whose display name already exists on Cloud Prem.

### 8. Membership gap CSV + ask path for MEMBERSHIPS — ALL or SELECT

Cloud's group memberships are the source of truth — **do not** ask the user to author this CSV by hand. Run:

```bash
python3 scripts/export_cloud_prem_group_membership_gap_csv.py
```

The script lists Cloud memberships and emits **only** rows where (a) the group exists on Cloud Prem (by display name), (b) the user exists on Cloud Prem (by email), and (c) the user is not yet a member of the Cloud Prem group. Surface the stderr summary — especially `N Cloud groups skipped because there's no Cloud Prem counterpart` — so the user knows whether they need to revisit step 7 for any group.

If 0 rows, tell the user no memberships need applying and move to verify. Otherwise, **ask the same branching question once more** — again independent of the previous two:

> I found **K** memberships from Cloud that are missing on Cloud Prem (filtered to users + groups that already exist on Cloud Prem). How do you want to apply them?
>
> **A) Apply ALL of them** — `--row-filter all`, sends a `PATCH /Groups/{id}` add for every row.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-group-membership-gap-selection.csv`, mark `y` on the rows you want, save, and I'll run with the default `--row-filter recommended`.
>
> Which would you like — A (all), or B (select)?

Wait for an explicit answer before continuing.

### 9. Add memberships (dry-run, then real)

Run the dry-run **and** the real run for the path the user chose in step 8:

```bash
# Path A — all rows
python3 scripts/scim_bulk_add_group_members.py --dry-run --row-filter all
python3 scripts/scim_bulk_add_group_members.py --row-filter all

# Path B — only rows the user marked with y
python3 scripts/scim_bulk_add_group_members.py --dry-run
python3 scripts/scim_bulk_add_group_members.py
```

The bulk script lists Cloud Prem users + groups once at startup and resolves every email and display name from those listings (no per-row SCIM filter queries). If errors include "no group on Cloud Prem" or "no user on Cloud Prem", the CSV is stale — re-run the membership export from step 8 to refresh it.

### 10. Verify

Re-run all three gap exports. Each one overwrites its canonical CSV; "header row only" (1 line via `wc -l`) means no remaining gaps.

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
python3 scripts/export_cloud_prem_group_gap_csv.py
python3 scripts/export_cloud_prem_group_membership_gap_csv.py

wc -l migration-artifacts/cloud-prem-user-gap-selection.csv
wc -l migration-artifacts/cloud-prem-group-gap-selection.csv
wc -l migration-artifacts/cloud-prem-group-membership-gap-selection.csv
```

All three should report 1 line (the header) except for rows the user **intentionally** skipped. Surface any remaining rows so the user can confirm they are expected.

### 11. Hand off to Phase 2 / Phase 3

Remind the user that:

- The principal map for Phase 3 (Cloud user UUID → Cloud Prem user UUID, Cloud group UUID → Cloud Prem group UUID) can be reconstructed any time by re-running the gap exports and joining on normalized email / `displayName`. They do not need to capture it manually now.
- Phase 2 ([cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)) does **not** depend on memberships being correct — it can proceed in parallel if convenient.

## Agent guardrails

- **Never** call any MCP tool from this skill. Cloud and Cloud Prem reads/writes go through the SCIM scripts only. (MCP is used in Phase 2 and Phase 3 skills.)
- **Never** print or commit SCIM tokens. When showing `.env` content, redact token values.
- **Always** dry-run before any SCIM POST/PATCH, surface the dry-run summary, and wait for the user to confirm before running for real.
- **Do not** edit a gap CSV on the user's behalf without their explicit instruction — the CSV is their selection tool.
- **Do not** create users or groups that the gap export did not surface. If the user asks for a one-off create, either add a row to the CSV first or stop and ask them to use Admin UI / SSO JIT.
- **Custom org roles** must already exist on Cloud Prem before `scim_bulk_provision_users.py` references them — the SCIM enterprise `role` attribute is rejected otherwise. If the user has custom roles, confirm they exist on Cloud Prem before running.
- **SSO/SCIM IdP push** may have already provisioned everything. If both gap CSVs are empty on the first run, congratulate the user and move on; do not invent work.

## Capability boundary

These scripts cover SCIM-supported operations: list/create users and groups, plus group membership PATCH. They do **not**:

- Grant resource access (that is Phase 3 and uses MCP, not SCIM).
- Deactivate users on Cloud Prem (out of scope for migration — handle via Admin UI).
- Migrate non-SCIM identity attributes such as custom IdP claims, manager hierarchies, or login MFA settings.
- Migrate API keys, personal access tokens, or session tokens.

If the user's request falls outside SCIM, stop and surface it as a gap rather than improvising.

## Related

- **Narrower variants** (for when the user only wants one of the three operations):
  - [cloud-to-cloud-prem-user-provisioning](../cloud-to-cloud-prem-user-provisioning/SKILL.md) — users only.
  - [cloud-to-cloud-prem-group-provisioning](../cloud-to-cloud-prem-group-provisioning/SKILL.md) — groups only.
  - [cloud-to-cloud-prem-group-membership-sync](../cloud-to-cloud-prem-group-membership-sync/SKILL.md) — memberships only.
- **Next in order:** application YAML and integration remapping: [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)
- **After apps exist on Cloud Prem:** resource access and sharing: [cloud-to-cloud-prem-permissions-migration](../cloud-to-cloud-prem-permissions-migration/SKILL.md)
- Setup, full command reference, and security: `README.md` at repository root (Phase 1 section).
