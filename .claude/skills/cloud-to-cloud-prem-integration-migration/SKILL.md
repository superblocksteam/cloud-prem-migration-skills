---
name: cloud-to-cloud-prem-integration-migration
description: >-
  Migrate Superblocks integrations (data source connections, plugin instances) from
  Superblocks Cloud to Cloud Prem **independently of any applications**. Uses two
  Superblocks MCP servers (Cloud + Cloud Prem) — list_integrations on both sides to
  compute a gap, then writes a selection CSV at
  migration-artifacts/cloud-prem-integration-gap-selection.csv (same migrate_to_cloud_prem
  column convention as the Phase 1 user/group/membership CSVs). The user chooses ALL or
  SELECT — the CSV selection is the consent signal, so the skill then iterates the
  candidate set autonomously and calls create_integration on Cloud Prem for each row with
  PLACEHOLDER credentials. The skill never reads, types, or transmits real secrets; surfaces
  each create result back to the user; and stops on errors instead of blindly continuing.
  After the loop the user must open Integrations on Cloud Prem and replace placeholders
  with real credentials. Pairs with cloud-to-cloud-prem-app-migration for the app-driven
  case where integrations are created as a side effect of moving an app's YAML. Does NOT
  migrate app code, permissions, or org settings.
---

# Cloud → Cloud Prem integration migration (Phase 2, integrations only)

This skill walks the user through migrating Superblocks **integrations** (data sources, plugin connections, secrets references) from Superblocks Cloud to Cloud Prem **without** touching any application code. Use it when:

- The customer wants integrations ready on Cloud Prem before any apps reference them (e.g. to test connectivity, share with admins for credential review).
- The customer has integrations that no application currently uses but they still want them on Cloud Prem.
- The customer is migrating apps later and wants to pre-stage integrations.

For the app-driven case (an app's YAML references integration UUIDs that need to be remapped during `superblocks upload`), use [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md). The app-migration skill still uses per-integration prompts in its §5 because the integrations there are surfaced one at a time from inside an app's YAML, with no upfront CSV — that's a different selection model from this skill.

## Prerequisites

- **Two Superblocks MCP servers** configured in your AI client (Claude Code's `.mcp.json` at the repo root, or Cursor's `~/.cursor/mcp.json`; see repo `README.md` → "Configure two Cursor MCP servers"). One process authenticated to **Cloud (SaaS)**, one to **Cloud Prem** (`<company>.superblocks.com`).
- Convention for server names in this skill: **`superblocks-cloud`** and **`superblocks-cloud-prem`**. If the user's MCP config uses different keys, follow **their** names and map: Cloud SaaS → Cloud MCP; Cloud Prem host → Cloud Prem MCP.
- The user **must** have credentials handy (in their password manager / vault) for any integration they want migrated — placeholder secrets get rejected by most data sources at runtime, so they'll need to replace them in the Cloud Prem UI immediately after.

## MCP routing (critical)

Every MCP tool call requires a **`base_url`** argument. Choose the MCP server that matches that origin's auth:

| Target | Example `base_url` | Call tools from |
|--------|--------------------|-----------------|
| Superblocks Cloud (SaaS) | `https://app.superblocks.com/` (or regional SaaS URL) | **Cloud** MCP (e.g. `superblocks-cloud`) |
| Cloud Prem | `https://<company>.superblocks.com/` | **Cloud Prem** MCP (e.g. `superblocks-cloud-prem`) |

Do **not** call Cloud Prem `base_url` tools on the Cloud MCP (or the reverse): each server process only has credentials for one `superblocksBaseUrl` in its `SUPERBLOCKS_AUTH_FILE`.

## Non-negotiable rules

`create_integration` creates real org resources on Cloud Prem. Even with placeholder secrets it counts as a high-impact write. The CSV-based selection in §4a is the **user's consent signal** — once the candidate set is established the skill iterates it autonomously and does not ask yes/no per row. But the following rules still apply unconditionally:

1. **Only call `create_integration` for rows in the candidate set from step 4a.** Never create an integration the user didn't put into the candidate set (either by choosing path A or by marking `y` in the CSV under path B). If the CSV is empty after step 4a, stop.

2. **Never** read, type, or transmit real secret values. Use `get_integration` on Cloud only to learn **shape** (auth type, required field names, `urlBase`, etc.). Do **not** copy secret values out of the Cloud response into chat, git, or the `create_integration` payload. Always use obvious placeholder strings.

3. **Never** accept real credentials from the user in chat. If they paste one, refuse the create and direct them to the Cloud Prem UI. Tell them to rotate the credential since it was exposed.

4. **Surface every create back to the user.** Per row in the candidate set: show the integration name + plugin_id you're about to send, the placeholder payload, the response from Cloud Prem, and the credential-update reminder. The user must be able to follow along and intervene at any time.

5. **Stop on errors — do not blindly continue.** If `create_integration` fails for a row, surface the error and ask the user whether to retry, skip, or stop the whole loop. Errors in the loop do not roll back rows that already succeeded.

6. **Every `create_integration` payload must set `enabledForV2: true`.** This flag is what makes the integration usable inside V2 / Code Mode applications on Cloud Prem. Do not omit it; do not copy `enabledForV2: false` over from the Cloud-side response. See §5c for the payload pattern.

## Step-by-step execution

### 1. Confirm both MCPs healthy

Call **`get_auth_status`** on **both** MCPs with each server's matching `base_url`. If either fails, stop and have the user fix MCP config + restart their client. Do not proceed with partial connectivity — the gap calculation requires both sides.

### 2. Inventory Cloud Prem first

On the **Cloud Prem** MCP, call **`list_integrations`** with the Cloud Prem `base_url`. Paginate with `limit` / `offset` if the response is truncated. Record `(name, plugin_id, kind, id)` tuples — this is the authoritative "already exists on Cloud Prem" list.

This **must** come before Cloud inventory so the agent can deduplicate, mirroring the no-duplicates rule in Phase 1.

### 3. Inventory Cloud

On the **Cloud** MCP, call **`list_integrations`** with the Cloud `base_url`. Paginate the same way. Record `(name, plugin_id, kind, id)` tuples.

### 4. Compute the gap and write the selection CSV

Build the list of Cloud integrations that are **missing** on Cloud Prem. Match keys, in priority order:

1. **`(name, plugin_id)` tuple, case-insensitive on name** — an integration is "already on Cloud Prem" only when **both** fields match. Same plugin_id with a different name → still missing. Same name with a different plugin_id → still missing (likely a different integration despite the name collision; ask the user).
2. **Plain name match (case-insensitive)** as a secondary signal only when the user explicitly says their tenant uses unique integration names.

Write the gap to `migration-artifacts/cloud-prem-integration-gap-selection.csv` using your filesystem write tool (the path is fixed — same convention as the Phase 1 gap CSVs). Columns:

| column | description |
|--------|-------------|
| `migrate_to_cloud_prem` | empty by default; user puts `y` to include this row in the migration (same column as the Phase 1 user / group / membership CSVs) |
| `name` | integration display name (from `list_integrations`) |
| `plugin_id` | e.g. `snowflake`, `postgres`, `rest_api` |
| `kind` | e.g. `database`, `api`, `secret_manager` (blank if the response doesn't expose it) |
| `cloud_integration_id` | Cloud SCIM UUID for traceability |
| `notes` | empty by default; user can annotate per row |

Example body for three missing integrations:

```csv
migrate_to_cloud_prem,name,plugin_id,kind,cloud_integration_id,notes
,analytics-warehouse,snowflake,database,8a3c1f...,
,crm-readonly,postgres,database,b41f9c...,
,salesforce-prod,rest_api,api,c9e2dd...,
```

Also surface the gap as a numbered table in chat so the user can see what's in the CSV without opening it:

| # | name | plugin_id / kind | Cloud `id` |
|---|------|------------------|------------|
| 1 | analytics-warehouse | snowflake | `8a3c…` |
| 2 | crm-readonly | postgres | `b41f…` |
| 3 | salesforce-prod | rest_api | `c9e2…` |

If 0 rows, do **not** write an empty CSV — congratulate the user (nothing to migrate) and move to step 7 (verify).

### 4a. Ask the user which path to take — ALL or SELECT

**Mandatory branching question.** Same pattern as the Phase 1 user / group / membership skills — the user picks how to choose which CSV rows will be created. The CSV (path B) **or** an explicit "all" (path A) **is the user's consent**; once a path is chosen, the skill iterates the candidate set autonomously through §5.

> I wrote **N** integrations to `migration-artifacts/cloud-prem-integration-gap-selection.csv`. How do you want to handle them?
>
> **A) Migrate ALL of them** — I'll iterate every row in the CSV and call `create_integration` on Cloud Prem for each with placeholder credentials. You'll see each create + result; you can interrupt at any time, but I won't ask yes/no per row.
>
> **B) Let me SELECT specific rows first** — open `migration-artifacts/cloud-prem-integration-gap-selection.csv`, put `y` in the `migrate_to_cloud_prem` column for the integrations you want, save, and I'll iterate only those.
>
> **C) Skip integration migration entirely** — stop here, don't create anything.
>
> Which would you like — A (all), B (select), or C (skip)?

Wait for an explicit answer. Accept short forms ("A", "all", "B", "select", "let me mark", "C", "skip", "none").

Then:

- **Path A (all):** candidate set = every row in the CSV. State this back: *"Migrating all N integrations. Starting with #1…"*
- **Path B (select):** wait for the user to confirm they've finished marking `y` and saved the file. Re-read the CSV; the candidate set = only rows where `migrate_to_cloud_prem` is truthy (case-insensitive `y`, `yes`, `true`, `1`, `x` — same matching the Phase 1 scripts use). State the resolved set back: *"Got it — migrating 2 of 3: **analytics-warehouse** and **salesforce-prod**. Starting with the first…"* If the user marked 0 rows, surface that and ask whether they meant to mark some or whether they want to switch to path C.
- **Path C (skip):** log "user opted to skip integration migration" and jump to step 7 (verify). Do not start the loop.
- **Vague answer / question / silence:** clarify and re-ask. Do not start the loop until the user has picked.

### 5. Create loop (iterate the candidate set autonomously)

For **each** row in the candidate set from step 4a (path A → every CSV row; path B → only `y`-marked rows), run the full cycle below. **No yes/no prompt per row** — the user already consented via the CSV. Process rows sequentially; the user can interrupt with "stop" at any time.

#### 5a. Announce the row

Tell the user what's about to happen, in one short line:

> **[3/5] Creating `analytics-warehouse`** (`plugin_id=snowflake`, Cloud id `8a3c…`) on Cloud Prem with placeholder credentials…

The `[N/M]` progress counter lets the user follow along across long runs. Do not pause for confirmation.

#### 5b. Gather shape from Cloud (no secrets)

On the **Cloud** MCP, call **`get_integration`** for this Cloud UUID. Read **only** the structural fields you need to build a valid `create_integration` payload on Cloud Prem:

- `plugin_id` / `kind`
- Auth type (basic, OAuth, API key, IAM role, etc.)
- Required field **names** and types (e.g. `host`, `port`, `database`, `username`, `password`, `urlBase`, `apiKeyHeader`)
- Non-secret defaults that should carry over (e.g. SSL mode, connection timeout, region defaults — when these are clearly non-sensitive)

**Do not** copy any field that holds a credential value — passwords, API keys, OAuth tokens, private keys, AWS secret keys, anything in a `secret` / `password` / `token` / `key` shaped field. If `get_integration` returns those values, ignore them; they go in the placeholder bucket below.

If `get_integration` requires extra docs to understand the plugin's required fields, check the public Superblocks plugin docs (https://docs.superblocks.com/integrations/) — do not infer from Cloud responses.

#### 5c. Build the placeholder payload

Construct the `create_integration` body for Cloud Prem. For every secret or credential field, use an obvious placeholder string such as:

```
PLACEHOLDER_UPDATE_IN_CLOUD_PREM_UI
```

For non-secret fields where the schema requires a syntactically valid value (e.g. a URL, port number, hostname), prefer:

- Public vendor base URLs (e.g. `https://api.example.com`) over the customer's real connection string — unless the user has explicitly supplied a real value for this step.
- The default port for the plugin type (`5432` for postgres, `3306` for mysql, etc.).
- The smallest valid string for free-form fields the schema marks required but doesn't constrain.

**Always set `enabledForV2: true` in the payload.** Cloud Prem integrations need this flag to be available inside V2 / Code Mode applications, and the Phase 2 app-migration skill expects every migrated integration to be V2-enabled before it tries to rebind app YAML to it. Setting it at create time avoids a follow-up `update_integration` round-trip. If the Cloud-side `get_integration` response also carries `enabledForV2: false` (older Cloud orgs sometimes do), **still** override to `true` on the Cloud Prem create — Cloud Prem assumes V2 by default.

Show the user the placeholder payload before sending — including the `enabledForV2: true` line — so they can sanity-check the shape. **Do not** include real secrets in the payload even if the user pastes them in chat; refuse and tell them to use the Cloud Prem UI instead.

#### 5d. `create_integration` on Cloud Prem

On the **Cloud Prem** MCP, call **`create_integration`** with the Cloud Prem `base_url` and the placeholder payload. Record the new Cloud Prem `id` returned in the response.

The agent now holds the pairing **Cloud `id` → Cloud Prem `id`** for **this** integration. If the user runs the app-migration skill later, this pairing will be reused there — but the agent does **not** need to keep a master list across all integrations for this skill's scope. (If the user explicitly asks for a mapping artifact, write a CSV to `migration-artifacts/integration-id-map.csv` with columns `name`, `plugin_id`, `cloud_id`, `cloud_prem_id`, `created_at`.)

#### 5e. Hand off the credential update (required)

After each successful `create_integration`, tell the user clearly:

- The new integration on Cloud Prem currently has **placeholder credentials / configuration**.
- They **must** open their **Cloud Prem** instance, go to **Integrations** (or the integration's edit screen), find the integration **by name**, and **enter real secrets and configuration** before any API / workflow / job using this integration will run.
- If they pasted any token / credential into chat at any point, treat it as exposed and **rotate** it before using it.

Mark the integration as **done** in your chat-side tracking table and move to the next row.

### 6. Per-integration error handling

If `create_integration` returns an error (4xx/5xx, schema validation, missing required field), surface the error to the user verbatim. Do **not** retry blindly. Common causes:

- **400 — required field missing:** the placeholder payload didn't cover something the plugin requires. Check `get_integration` shape on Cloud again, or the public plugin docs, and ask the user how to fill the missing field with a non-sensitive placeholder. Then retry **once**.
- **409 — already exists:** another integration with the same name+plugin_id appeared on Cloud Prem between step 2 and now. Re-run `list_integrations` on Cloud Prem to refresh state, drop the row from the gap list, and move on.
- **401 / 403:** the Cloud Prem MCP server's `SUPERBLOCKS_AUTH_FILE` doesn't have integration-write permissions. Stop and have the user fix that before continuing.

Do not roll back a partially successful gap loop — completed integrations stay created. Surface the remaining error rows as "skipped due to errors" at the end.

### 7. Verify

Re-run **`list_integrations`** on the **Cloud Prem** MCP and confirm every integration the user approved is now present (by name + plugin_id). Surface the count: how many were in the gap, how many were created, how many were skipped or errored.

If the user wants a permanent record, write the mapping CSV from step 5d.

### 8. Hand off

Tell the user:

- **For each created integration, real credentials still need to be entered in the Cloud Prem UI** before the integration is usable at runtime. Reiterate this even if it was said earlier — it's the most common follow-through failure.
- If they're planning to migrate apps next, the integration UUIDs they just got on Cloud Prem are the values that need to land in app YAML. Point them at [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md), where §5b becomes the "use existing Cloud Prem integration" path (since the integration now exists, the agent will pick that path automatically instead of creating another one).
- If they want resource-level access control on the new integrations (who can use them), point them at [cloud-to-cloud-prem-permissions-migration](../cloud-to-cloud-prem-permissions-migration/SKILL.md).

## Agent guardrails

- **Never** print or commit real secret values from `get_integration` responses. If the response includes a credential, ignore it — you only need the field **name**, not the value.
- **Never** accept real credentials from the user in chat. If they paste one, refuse the create and direct them to the Cloud Prem UI. Tell them to rotate the credential since it was exposed.
- **The CSV (or path A) is the user's consent.** Iterate the candidate set without asking yes/no per row. The user can interrupt at any time by saying "stop"; honor that immediately and report which rows finished vs. didn't.
- **Always** use the placeholder payload pattern. Even if the user supplies "test credentials" that they say are safe to use, prefer the placeholder — it's easier to spot in the UI and forces the credential-update step.
- **Always** show each row's announcement + result. The user must be able to follow along even though there's no per-row prompt.
- **Always** call `list_integrations` on **Cloud Prem first** before Cloud, so you can detect duplicates that were created by some other path between runs.
- **Stop on errors.** If a row fails, surface the error and ask the user how to proceed (retry / skip / stop) before touching the next row — never silently keep going through errors.
- **Do not** delete or modify existing Cloud Prem integrations from this skill. If the user wants to update a misconfigured one, direct them to the Cloud Prem UI or to a future `update_integration`-focused skill.
- **Do not** carry state between sessions. If the user comes back later, re-run the inventory and gap from step 2.

## Capability boundary

This skill covers `list_integrations` + `get_integration` (read-only on Cloud, shape-only) + `create_integration` (write on Cloud Prem, placeholders only). It does **not**:

- Migrate any application code (see [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)).
- Migrate workflows or jobs (those are separate resource types; not yet covered by a dedicated skill).
- Set resource-level RBAC on the new integrations (see [cloud-to-cloud-prem-permissions-migration](../cloud-to-cloud-prem-permissions-migration/SKILL.md)).
- Update or refresh credentials on existing Cloud Prem integrations.
- Delete integrations on either side.
- Transmit real secrets from Cloud to Cloud Prem.

## Related

- **App-driven integration migration:** [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md) §5 — creates integrations as a side effect of moving an app's YAML. That skill still uses per-integration yes/no prompts (no CSV-based consent step), because integrations there are surfaced one at a time from inside an app's YAML.
- **Permissions next:** [cloud-to-cloud-prem-permissions-migration](../cloud-to-cloud-prem-permissions-migration/SKILL.md) — share the new integrations with the right users/groups on Cloud Prem.
- **Phase 1 prerequisites:** users and groups on Cloud Prem before any permission grants. See the [cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md) family.
- Setup, MCP server configuration, and security: `README.md` at repository root.
