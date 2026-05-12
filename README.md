# Cloud → Cloud Prem migrations

End-to-end workflow for moving an organization from **Superblocks Cloud** (SaaS) to **Cloud Prem** (self-hosted). This repo gives you:

- **Phase 1 — Users & Groups:** Python scripts that talk to SCIM 2.0 on both deployments. You run them directly from this repo (no AI client required) — or, if you'd rather have an AI agent run them for you, attach the matching Agent Skill (master skill for end-to-end Phase 1, or a narrower per-operation skill: users / groups / memberships).
- **Phase 2 — Applications & Integrations:** an Agent Skill that walks you through `superblocks pull`, integration UUID remapping, and `superblocks upload`.
- **Phase 3 — Resource Permissions:** an Agent Skill that mirrors Cloud RBAC onto Cloud Prem principal and resource IDs.

Skills are shipped for both **Claude Code** (under `.claude/skills/`, plus a project-wide `CLAUDE.md`) and **Cursor** (under `.cursor/skills/`, plus rules under `.cursor/rules/`). The two trees mirror each other so the migration workflow is identical regardless of which AI client you use.

## Migration order

Run the phases in this order — each one depends on the artifacts produced by the previous phase.


| #   | Phase                        | How you run it                                            | Depends on                          |
| --- | ---------------------------- | --------------------------------------------------------- | ----------------------------------- |
| 1   | Users & Groups & memberships | `scripts/*.py` from this repo (this README)               | `.env` + SCIM tokens                |
| 2   | Applications & Integrations  | Agent skill: `cloud-to-cloud-prem-app-migration` (or `cloud-to-cloud-prem-integration-migration` for integrations-only) | Phase 1 + CLI + two MCP servers     |
| 3   | Resource Permissions         | Agent skill: `cloud-to-cloud-prem-permissions-migration`  | Phase 1 + Phase 2 + two MCP servers |


### Why this order — the dependency graph

```
Users ─┐
       ├─→ Group memberships ─┐
Groups ┘                      │
                              ├─→ Resource Permissions
Integrations ─┐               │
              ├─→ Applications┘
              │
              (apps reference integrations by UUID)
```

Read left-to-right: each box can only be migrated once **every** arrow pointing into it has been satisfied.

- **Users** and **Groups** are independent (top of Phase 1) — neither depends on the other.
- **Group memberships** need both **Users** and **Groups** to exist on Cloud Prem first, which is why Phase 1 runs in the order users → groups → memberships.
- **Integrations** are independent of Phase 1 — they can be staged on Cloud Prem before any users or apps exist (use the `cloud-to-cloud-prem-integration-migration` skill if you want to migrate them first).
- **Applications** depend on integrations: app YAML references integrations by UUID, so each Cloud integration UUID must be mapped to a Cloud Prem UUID before the app can run.
- **Resource Permissions** is the "join" of everything else — to grant access, you need (a) a Cloud Prem principal (user or group from Phase 1), (b) a Cloud Prem resource (app or integration from Phase 2), and (c) the Cloud → Cloud Prem ID maps captured during the earlier phases.

The identity track (Users → Groups → Memberships) and the resource track (Integrations → Applications) can run **in parallel** if you have two operators, since neither depends on the other. They only have to converge before Phase 3.

Phases 2 and 3 are operated through Agent chat by attaching the matching skill (see [Phase 2](#phase-2--applications--integrations-agent-skill) and [Phase 3](#phase-3--resource-permissions-agent-skill) below).

---

## One-time setup

### 1. Install prerequisites

```bash
# Python 3.10+ (stdlib only — no pip install required for the SCIM scripts)
python3 --version

# Superblocks CLI (needed for Phase 2)
npm install -g @superblocksteam/cli
```

### 2. Get your Organization SCIM tokens

The SCIM scripts use Organization access tokens issued in each deployment's **org settings** (SCIM/org-admin capable). See [Superblocks SCIM docs](https://docs-legacy.superblocks.com/administration/security/scim/).

- Issue one token in your **Superblocks Cloud** org settings.
- Issue a separate token in your **Cloud Prem** org settings.

These are different from your CLI session token and from any MCP server bearer — both of which will return HTTP 401 on `/scim/v2`.

### 3. Create the shared `.env`

At the repo root, copy the template below and fill in real values. The file is gitignored.

```env
# Superblocks Cloud (SaaS) - source environment
SUPERBLOCKS_CLOUD_BASE_URL=https://app.superblocks.com
SUPERBLOCKS_CLOUD_AUTH_FILE=$HOME/.superblocks/profiles/cloud/auth.json
SUPERBLOCKS_SCIM_TOKEN_CLOUD=<paste-cloud-org-scim-token>

# Superblocks Cloud Prem - target environment
SUPERBLOCKS_CLOUD_PREM_BASE_URL=https://<your-tenant>.superblocks.com
SUPERBLOCKS_CLOUD_PREM_AUTH_FILE=$HOME/.superblocks/profiles/cloud_prem/auth.json
SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM=<paste-cloud-prem-org-scim-token>
```

`$HOME` and a leading `~` in the auth-file paths are expanded by the loader. Tokens are **host-specific** — there is no single-token fallback. The Cloud (SaaS) URL is typically `https://app.superblocks.com` (use your regional URL if applicable, e.g. `https://eu.superblocks.com`).

### 4. Create CLI auth profiles (needed for Phases 2 and 3)

```bash
source scripts/sb-auth-profile.sh

sb_auth_profile cloud
superblocks config set domain app.superblocks.com    # or your regional SaaS URL
superblocks login

sb_auth_profile cloud_prem
superblocks config set domain <your-tenant>.superblocks.com
superblocks login

sb_auth_profile list   # sanity check
```

`sb_auth_profile cloud` exports `SUPERBLOCKS_AUTH_FILE` from `SUPERBLOCKS_CLOUD_AUTH_FILE` in `.env`; `sb_auth_profile cloud_prem` does the same for `SUPERBLOCKS_CLOUD_PREM_AUTH_FILE`. You must `source` the script (not run it) so the variable survives in your shell.

### 5. Configure two Cursor MCP servers (needed for Phases 2 and 3)

Add **two** entries to `~/.cursor/mcp.json`, each with its own `SUPERBLOCKS_AUTH_FILE` pointing at the **same absolute paths** in your `.env`:

```json
{
  "mcpServers": {
    "superblocks-cloud": {
      "command": "/FULL/PATH/TO/superblocks",
      "args": ["mcp", "serve"],
      "env": {
        "SUPERBLOCKS_AUTH_FILE": "/FULL/PATH/TO/.superblocks/profiles/cloud/auth.json"
      }
    },
    "superblocks-cloud-prem": {
      "command": "/FULL/PATH/TO/superblocks",
      "args": ["mcp", "serve"],
      "env": {
        "SUPERBLOCKS_AUTH_FILE": "/FULL/PATH/TO/.superblocks/profiles/cloud_prem/auth.json"
      }
    }
  }
}
```

Run `which superblocks` to find the absolute binary path; Cursor's GUI environment often can't resolve `superblocks` on `PATH`. After editing, fully quit and reopen Cursor. Verify each server is healthy by calling `get_auth_status` in Agent chat with the matching `base_url`.

---

## Phase 1 — Users & Groups (run the scripts)

All commands run from this repo's root. Every script auto-loads `./.env` and exits with a clear error if a required value is missing.

> **Prefer to drive this with an AI agent?** All of Phase 1 (and the individual user / group / membership operations) is also wrapped as Agent Skills for both **Claude Code** (`.claude/skills/`) and **Cursor** (`.cursor/skills/`). See [Agent Skills reference](#agent-skills-reference) for the full list and example prompts. The skills walk you through the exact same commands documented below — they just do the script invocation, summary, and confirmation loop for you.

All Phase 1 scripts use **fixed file paths** under `migration-artifacts/` — you cannot redirect them. Every export overwrites its target file, and every bulk script reads exactly that file. The three canonical artifacts are:

| File | Produced by | Consumed by |
|------|-------------|-------------|
| `migration-artifacts/cloud-prem-user-gap-selection.csv` | `export_cloud_prem_user_gap_csv.py` | `scim_bulk_provision_users.py` |
| `migration-artifacts/cloud-prem-group-gap-selection.csv` | `export_cloud_prem_group_gap_csv.py` | `scim_bulk_provision_groups.py` |
| `migration-artifacts/cloud-prem-group-membership-gap-selection.csv` | `export_cloud_prem_group_membership_gap_csv.py` | `scim_bulk_add_group_members.py` |

### Step 1.1 — Export the user gap CSV

Compares Cloud and Cloud Prem with SCIM `GET /Users` (paginated) and writes the rows that exist on Cloud but not on Cloud Prem. Deactivated Cloud users (`active=false`) are filtered out.

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
```

Writes `migration-artifacts/cloud-prem-user-gap-selection.csv`. Columns: `migrate_to_cloud_prem`, `name`, `email`, `cloud_user_id`, `cloud_org_role`, `cloud_status`, `notes`.

### Step 1.2 — Review the CSV

Open `migration-artifacts/cloud-prem-user-gap-selection.csv` and mark the rows you want to migrate by putting `y` in the `migrate_to_cloud_prem` column. Leave `cloud_org_role` populated when present (the bulk provision script maps it to the SCIM enterprise `role` attribute). Add notes to track decisions per row.

To migrate **every** active Cloud user that's missing on Cloud Prem, you can skip this step and pass `--row-filter all` to the next command.

### Step 1.3 — Dry-run, then provision users

```bash
# Dry-run: prints the SCIM POST /Users payloads it would send
python3 scripts/scim_bulk_provision_users.py --dry-run

# Real run (default --row-filter recommended honors the y marks from Step 1.2)
python3 scripts/scim_bulk_provision_users.py

# Or, to provision every row with an email regardless of the y marks
python3 scripts/scim_bulk_provision_users.py --row-filter all
```

The script skips users that already exist on Cloud Prem (matched by `emails.value` or `userName`). If a row has no `cloud_org_role`, pass `--default-org-role developer` (or `admin`, `end_user`, or a custom org role key) to set one.



### Step 1.4 — Export the group gap CSV

Compares Cloud and Cloud Prem with SCIM `GET /Groups` (paginated) and writes the rows that exist on Cloud but not on Cloud Prem (case-insensitive `displayName` match).

```bash
python3 scripts/export_cloud_prem_group_gap_csv.py
```

Writes `migration-artifacts/cloud-prem-group-gap-selection.csv`. Columns: `migrate_to_cloud_prem`, `display_name`, `cloud_group_id`, `notes`.

### Step 1.5 — Review the CSV

Open `migration-artifacts/cloud-prem-group-gap-selection.csv` and mark the rows you want to migrate by putting `y` in the `migrate_to_cloud_prem` column. Add notes to track decisions per row.

To migrate **every** Cloud group that's missing on Cloud Prem, you can skip this step and pass `--row-filter all` to the next command.

### Step 1.6 — Dry-run, then provision groups

```bash
# Dry-run: prints the SCIM POST /Groups payloads it would send
python3 scripts/scim_bulk_provision_groups.py --dry-run

# Real run (default --row-filter recommended honors the y marks from Step 1.5)
python3 scripts/scim_bulk_provision_groups.py

# Or, to provision every group in the gap CSV regardless of marks
python3 scripts/scim_bulk_provision_groups.py --row-filter all
```

The script skips group display names that already exist on Cloud Prem.

### Step 1.7 — Export the group-membership gap CSV

Compares Cloud and Cloud Prem memberships and emits only the user/group pairs where:

1. The Cloud group has a Cloud Prem counterpart (case-insensitive `displayName` match), AND
2. The user has been migrated (case-insensitive email match), AND
3. The user is NOT already a member of the Cloud Prem group.

```bash
python3 scripts/export_cloud_prem_group_membership_gap_csv.py
```

Writes `migration-artifacts/cloud-prem-group-membership-gap-selection.csv`. Columns: `migrate_to_cloud_prem`, `group_display_name`, `user_email`, `cloud_group_id`, `cloud_user_id`, `notes`.

The script never invents memberships — Cloud is the source of truth. If a Cloud group hasn't been provisioned on Cloud Prem yet (Step 1.6 still pending for that group), it is skipped and counted in stderr so you can come back after creating it.

### Step 1.8 — Review the CSV

Open `migration-artifacts/cloud-prem-group-membership-gap-selection.csv` and mark the rows you want to apply by putting `y` in the `migrate_to_cloud_prem` column. Add notes to track decisions per row.

To apply **every** missing membership in the gap CSV, you can skip this step and pass `--row-filter all` to the next command.

### Step 1.9 — Dry-run, then add memberships

```bash
# Dry-run: prints the SCIM PATCH /Groups/{id} adds it would send
python3 scripts/scim_bulk_add_group_members.py --dry-run

# Real run (default --row-filter recommended honors the y marks from Step 1.8)
python3 scripts/scim_bulk_add_group_members.py

# Or, to apply every row in the CSV regardless of marks
python3 scripts/scim_bulk_add_group_members.py --row-filter all
```

At startup the script lists `/Users` and `/Groups` on Cloud Prem and uses those listings to resolve every email + display name in the CSV. This avoids per-row SCIM `filter=` queries (which behave inconsistently across tenants) and matches the same approach the export script already uses. `skip member` lines mean the user is already in that group on Cloud Prem.

### Step 1.10 — Verify

Re-run all three gap exports. Each one overwrites its canonical CSV; success is "header row only" (no remaining gap rows you didn't intentionally skip).

```bash
python3 scripts/export_cloud_prem_user_gap_csv.py
python3 scripts/export_cloud_prem_group_gap_csv.py
python3 scripts/export_cloud_prem_group_membership_gap_csv.py

wc -l migration-artifacts/cloud-prem-user-gap-selection.csv
wc -l migration-artifacts/cloud-prem-group-gap-selection.csv
wc -l migration-artifacts/cloud-prem-group-membership-gap-selection.csv
# 1 line each = header only = success
```

Capture the post-provisioning gap CSVs (or the SCIM IDs they expose) — Phase 3 needs the **Cloud user UUID → Cloud Prem user UUID** and **Cloud group UUID → Cloud Prem group UUID** maps. Matching by normalized email (users) and `displayName` (groups) reconstructs them from a fresh gap export.

---

## Phase 2 — Applications & Integrations (Agent skill)

This phase uses the Superblocks CLI to pull and upload application code, and **two** Superblocks MCP servers (Cloud + Cloud Prem) to discover, map, and create integrations. Make sure Phase 1 is done and Steps 4 + 5 of [One-time setup](#one-time-setup) are complete.

You have a choice between two paths through Phase 2. Both end with apps running on Cloud Prem against real integrations; they differ in whether you stage integrations upfront or migrate them on-demand as apps reference them.

### Path A — Integration-first, then apps

Use when you want integrations staged on Cloud Prem in one batch (e.g. so the data team can validate connectivity / replace credentials before any app is touched), or when you have integrations that aren't yet referenced by any app you'll migrate.

1. **Migrate integrations as a batch.** Attach the integration-only skill:
    > Follow `.claude/skills/cloud-to-cloud-prem-integration-migration/SKILL.md` (or the `.cursor/...` twin).

    The skill lists integrations on both deployments, writes a gap CSV at `migration-artifacts/cloud-prem-integration-gap-selection.csv` (same `migrate_to_cloud_prem` column convention as Phase 1), and lets you choose **ALL** or **SELECT** specific rows. Once you've picked, the skill iterates the candidate set autonomously and calls `create_integration` on Cloud Prem for each row with **placeholder credentials** and `enabledForV2: true`.
2. **Replace placeholder credentials in the Cloud Prem UI.** Before any app actually runs, open Integrations → for each newly-created integration, enter real secrets / connection settings.
3. **Migrate apps.** Now attach the app skill:
    > Follow `.claude/skills/cloud-to-cloud-prem-app-migration/SKILL.md`.

    Because all integrations already exist on Cloud Prem, the agent will take the §5b "map to existing Cloud Prem integration" path for each Cloud UUID it encounters in YAML — no more `create_integration` calls needed.

### Path B — App-driven, integrations created on-demand

Use when the customer wants to migrate one app end-to-end at a time, when most integrations are app-scoped (you don't want unused ones lying around on Cloud Prem), or when you don't have a clean list of integrations to pre-stage.

1. **Migrate the app.** Attach the app skill:
    > Follow `.claude/skills/cloud-to-cloud-prem-app-migration/SKILL.md`.
2. The skill walks you through, per app:
    - `sb_auth_profile cloud` → `superblocks pull` the source app.
    - Confirm source vs target `application_mode` matches (hard blocker if not).
    - Rebind `.superblocks/superblocks.json` to the Cloud Prem application UUID.
    - Walk integration UUIDs **one at a time**: map to an existing Cloud Prem integration (§5b) **or** create a new one with placeholder credentials + `enabledForV2: true` and remap YAML (§5c). Each new integration requires its own explicit yes/no — there's no batch-create here, by design.
    - `sb_auth_profile cloud_prem` → `superblocks upload` → optional `superblocks dev --upload-first` for live debug.
3. **Replace placeholder credentials in the Cloud Prem UI** for every integration the agent created. This is where most "the migrated app fails with a 401" reports come from.

### Which path should I pick?

| Question | Path A — Integration-first | Path B — App-driven |
|----------|----------------------------|---------------------|
| **How does the agent surface integrations to you?** | CSV with `migrate_to_cloud_prem` column; you can mark a subset and walk away while the loop runs. | Inline, one UUID at a time, as the agent scans app YAML. |
| **How much per-integration prompting?** | None after you pick the CSV rows — the loop iterates autonomously, but surfaces each create so you can interrupt. | Every new integration gets its own yes/no prompt (no batch-create). |
| **Where do credentials get filled in?** | Cloud Prem UI, after the integration-only batch finishes — before any app runs. | Cloud Prem UI, after the app's upload — you can defer until you actually need that integration. |
| **Best for…** | Multiple apps sharing the same integrations; data-team-led credential setup; pre-staging before any app exists on Cloud Prem. | One-app-at-a-time migrations; minimizing unused integrations on Cloud Prem; tight coupling between app YAML and integration creation. |
| **Worst for…** | Single-app migrations where most integrations are unique to that app. | Many apps sharing the same handful of integrations (you'd answer the same prompt over and over). |

You can also **mix** them: run Path A first for the obvious shared integrations (data warehouses, identity providers, internal APIs), then run Path B for each app — most of the §5c `create_integration` prompts will be skipped because §5b matches an existing Cloud Prem integration.

After either path, the new integrations are usable in V2 / Code Mode apps (the skills set `enabledForV2: true` at create time). Full step-by-step instructions, the per-integration confirmation rules for Path B, and the CSV consent model for Path A live in the two skill files:

- [`.claude/skills/cloud-to-cloud-prem-integration-migration/SKILL.md`](.claude/skills/cloud-to-cloud-prem-integration-migration/SKILL.md) (Path A)
- [`.claude/skills/cloud-to-cloud-prem-app-migration/SKILL.md`](.claude/skills/cloud-to-cloud-prem-app-migration/SKILL.md) (Path B)

(Cursor users substitute `.cursor/skills/...` for the path.)

---

## Phase 3 — Resource Permissions (Agent skill)

This phase mirrors Cloud's resource-level RBAC (applications, integrations, folders, workflows, jobs, etc.) onto Cloud Prem. It depends on Phases 1 and 2 being complete — all principals **and** all resources must exist on Cloud Prem first.

1. In Agent chat, attach the skill explicitly:
  > Follow `.claude/skills/cloud-to-cloud-prem-permissions-migration/SKILL.md` (or the `.cursor/...` twin).
2. The agent will:
  - Read Cloud RBAC with `get_application_access` / `list_rbac_assignments` on the Cloud MCP.
  - Read current Cloud Prem state on the Cloud Prem MCP to avoid duplicate grants.
  - Translate principals via the user/group maps from Phase 1, and resource IDs from Phase 2.
  - Apply `grant_resource_access` / `update_resource_access` / `revoke_resource_access` on the **Cloud Prem** MCP only.
3. Verify by re-reading `get_application_access` and `list_rbac_assignments` on Cloud Prem.

Full step-by-step instructions are in [`.claude/skills/cloud-to-cloud-prem-permissions-migration/SKILL.md`](.claude/skills/cloud-to-cloud-prem-permissions-migration/SKILL.md) (or the Cursor twin under `.cursor/skills/`).

---

## Repository layout (reference)

| Path                   | Purpose                                                                  |
| ---------------------- | ------------------------------------------------------------------------ |
| `.env`                 | Shared, gitignored config (base URLs, CLI auth-file paths, SCIM tokens). |
| `scripts/`             | Phase 1 SCIM scripts (exports, bulk provisioning, shared lib) and the `sb-auth-profile.sh` CLI profile helper. |
| `CLAUDE.md`            | Project-wide context auto-loaded by Claude Code (terminology, where to find the workflow). |
| `.claude/skills/`      | **Claude Code** Agent Skills (one master + three narrower for Phase 1, plus Phase 2 and Phase 3). See [Agent Skills reference](#agent-skills-reference). |
| `.cursor/skills/`      | **Cursor** Agent Skills — same skill set as `.claude/skills/`, kept in sync. |
| `.cursor/rules/`       | Project-wide Cursor rules (mirrors `CLAUDE.md`).                         |
| `migration-artifacts/` | Canonical output directory for gap-selection CSVs — the three Phase 1 CSVs (users / groups / memberships) plus `cloud-prem-integration-gap-selection.csv` written by the Phase 2 integration migration skill. |

## Agent Skills reference

Each phase of the migration is wrapped as an Agent Skill, shipped for both **Claude Code** (`.claude/skills/<name>/SKILL.md`) and **Cursor** (`.cursor/skills/<name>/SKILL.md`). Open this repo in your AI client of choice — Claude Code auto-discovers `.claude/skills/`; Cursor auto-discovers `.cursor/skills/`. Then attach the skill explicitly in the chat.

| Skill name | Goal | Example chat |
| ---------- | ---- | ------------ |
| `cloud-to-cloud-prem-users-groups-migration` | Full end-to-end Phase 1 (users + groups + memberships) in one workflow. | _"Run the full Phase 1 migration — follow `.claude/skills/cloud-to-cloud-prem-users-groups-migration/SKILL.md`"_ |
| `cloud-to-cloud-prem-user-provisioning` | Phase 1, **users only** — export the user gap CSV, review, then SCIM `POST /Users`. | _"Migrate just the users from Cloud to Cloud Prem"_ |
| `cloud-to-cloud-prem-group-provisioning` | Phase 1, **groups only** — export the group gap CSV, review, then SCIM `POST /Groups`. | _"Sync the groups from Cloud to Cloud Prem; users are already done"_ |
| `cloud-to-cloud-prem-group-membership-sync` | Phase 1, **memberships only** — export the membership gap CSV (Cloud is source of truth), review, then SCIM `PATCH /Groups/{id}`. | _"Apply the missing group memberships on Cloud Prem"_ |
| `cloud-to-cloud-prem-integration-migration` | Phase 2, **integrations only** — list integrations on both sides, write a gap CSV at `migration-artifacts/cloud-prem-integration-gap-selection.csv` (same `migrate_to_cloud_prem` column convention as Phase 1), then call `create_integration` on Cloud Prem for every selected row with placeholder credentials. The CSV is the consent signal — no per-row yes/no — but each create is announced and the user can interrupt at any time. Use when you want integrations staged on Cloud Prem before any app references them. | _"Move my integrations to Cloud Prem without touching any apps yet"_ |
| `cloud-to-cloud-prem-app-migration` | Phase 2 — move application code with `superblocks pull` / `upload`, remap integration UUIDs one at a time, optionally `create_integration` on Cloud Prem with placeholder credentials. | _"Migrate this app from Cloud to Cloud Prem and remap its integrations"_ |
| `cloud-to-cloud-prem-permissions-migration` | Phase 3 — mirror Cloud resource RBAC (apps, integrations, folders, etc.) onto Cloud Prem principal + resource IDs. | _"Now grant the same access on Cloud Prem that everyone had in Cloud"_ |

When prompting, substitute `.cursor/skills/...` for the path if you're driving the migration in Cursor.

All Phase 1 skills run purely against the SCIM scripts in `scripts/` — no MCP server required. Phase 2 and Phase 3 do use MCP; see [Step 5 — Configure two Cursor MCP servers](#5-configure-two-cursor-mcp-servers-needed-for-phases-2-and-3) (the same MCP servers work for Claude Code — just declare them in Claude Code's `.mcp.json` at the repo root instead of Cursor's `~/.cursor/mcp.json`).


## Common operations

**Re-run a single phase against a different tenant**

Edit `.env` to point `SUPERBLOCKS_CLOUD_PREM_BASE_URL`, `SUPERBLOCKS_CLOUD_PREM_AUTH_FILE`, and `SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM` at the new tenant. Re-source `sb-auth-profile.sh` so the CLI picks up the new auth path. MCP requires updating `~/.cursor/mcp.json` and restarting Cursor.

**Skip the shared `.env` for a single run**

Pass `--no-dotenv` to any SCIM script and supply `--base-url`, `--token` / `--token-file`, etc. directly on the command line.

**Use a non-default profile directory**

```bash
export SUPERBLOCKS_PROFILES_DIR="$HOME/.config/superblocks-profiles"
source scripts/sb-auth-profile.sh
```

## Security

- **Never commit** `.env`, `~/.superblocks/**/auth.json`, or any token file. The repo's `.gitignore` covers `.env` but not files outside the repo.
- The CLI typically writes `auth.json` at mode 600 on Unix — keep it that way.
- SCIM tokens are organization-wide. Treat them like API keys: rotate after any exposure (chat paste, log dump, screen share).

## Related infrastructure repos

This repository covers **application code + integration ID remapping** (Phase 2), **users and groups** (Phase 1), and **resource permissions on Cloud Prem** (Phase 3). Deploying or operating the Cloud Prem stack itself (Kubernetes, Helm, agents) lives in other repositories such as `dedicated-deployment` and `cloud-prem-deployments`.