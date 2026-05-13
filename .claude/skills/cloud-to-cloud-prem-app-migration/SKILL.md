---
name: cloud-to-cloud-prem-app-migration
description: >-
  Migrate a Superblocks application from Superblocks Cloud (SaaS) to a Cloud Prem
  (self-hosted) instance using the Superblocks CLI (pull from Cloud, then upload to
  Cloud Prem via either `superblocks upload` or `superblocks dev --upload-first`;
  the two commands sometimes fail to render the app on first try, so the skill's
  recovery is to fall back to the OTHER command and refresh — see §7. For
  `dev --upload-first` the user must run `npm install` in the app directory first
  if there's a package.json), plus two Superblocks MCP servers (Cloud vs Cloud
  Prem) for integration discovery, mapping, and optional creation of missing
  integrations on Cloud Prem (placeholders + user credential update). Confirm the
  Cloud Prem template app type matches the Cloud source BEFORE upload — by
  inspecting the pulled source directory's on-disk layout (3.0 / Fullstack App has
  a client/server split; 2.0 / Application does not). Do NOT rely on
  application_mode from get_application_structure for this check — it returns 2.0
  for both types. Use Clark in-app to debug after dev. Use when moving apps between SaaS
  and enterprise domains, remapping integration UUIDs one at a time (no upfront
  full mapping table), or onboarding a customer from Cloud to Cloud Prem.
---

# Cloud → Cloud Prem application migration

This workflow moves **application code** (APIs, pages, workflows on disk) from a **Superblocks Cloud** org to a **Cloud Prem** org. Integration IDs are **organization-scoped UUIDs**; **each** Cloud UUID still referenced in YAML must **eventually** be replaced with the matching Cloud Prem org’s integration id for that step to run correctly on Cloud Prem (you may **`superblocks upload`** incrementally while work remains — see **§6** / **§7**). **Remapping is done one Cloud integration UUID at a time:** for each UUID, agree the single **`cloud_uuid` → `cloud_prem_uuid`** pairing with the user (or create on Cloud Prem per **§5c**), **rewrite the app files for that UUID immediately**, then move to the next. The user does **not** need to supply or approve a **full mapping table** up front. The **target empty app on Cloud Prem** must be the same **app type** as the Cloud app you pulled — determined by the **on-disk directory layout** of the pulled source (see **§3a**, which explains why `application_mode` from `get_application_structure` cannot be trusted for this). A mismatched template type is a hard blocker until the user recreates the Cloud Prem app as the correct type.

When a Cloud integration used by the app **does not yet exist** on Cloud Prem, the agent may **create** it on Cloud Prem using MCP with **placeholder** secrets/configuration, **remap** the new Cloud Prem integration id into the app, then direct the user to **replace placeholders with real credentials** in the Cloud Prem UI — but only under the **strict per-integration confirmation rules** in **§5c** below.

## Prerequisites

- [Superblocks CLI](https://www.npmjs.com/package/@superblocksteam/cli): `npm install -g @superblocksteam/cli`
- **Two** Superblocks MCP servers configured in your AI client (Claude Code's `.mcp.json` at the repo root, or Cursor's `~/.cursor/mcp.json`; see repo `README.md` → "Configure two MCP servers"). Each entry is a **remote MCP** — a `url` pointing at the deployment's hosted endpoint (`https://<host>/mcp`) plus a `headers.Authorization` Bearer token issued in that deployment. Two entries total: one for **Cloud (SaaS)**, one for **Cloud Prem** (`<company>.superblocks.com`).
- Convention for server names in this skill: **`superblocks-cloud`** and **`superblocks-cloud-prem`**. If the user's MCP config uses different keys, follow **their** names and map: Cloud SaaS → Cloud MCP; Cloud Prem host → Cloud Prem MCP.

## MCP routing (critical)

Superblocks MCP tools still require a **`base_url`** argument. **Choose the MCP server that matches that origin’s auth:**

| Target | Example `base_url` | Call tools from |
|--------|--------------------|-----------------|
| Superblocks Cloud (SaaS) | `https://app.superblocks.com/` (or regional SaaS URL) | **Cloud** MCP (e.g. `superblocks-cloud`) |
| Cloud Prem | `https://<company>.superblocks.com/` | **Cloud Prem** MCP (e.g. `superblocks-cloud-prem`) |

Do **not** call Cloud Prem `base_url` tools on the Cloud MCP (or the reverse): each entry is bound to a single deployment via its `url` + Bearer token, and tokens are not interchangeable.

If your MCP client lists duplicate tool names (same tool from two servers), pick the server column / group that matches the row above.

**Connection failed:** verify the entry's `url` ends in `/mcp` (e.g. `https://<host>/mcp`) and that the `headers.Authorization` Bearer is a token from that specific deployment. Restart the AI client after editing the config. Common causes are a stale or wrong-deployment Bearer (HTTP 401) and a malformed URL.

## 1) Two “profiles” (AWS-style) for the CLI

The CLI does **not** ship named profiles. Use **`SUPERBLOCKS_AUTH_FILE`** so each environment has its own `auth.json` (token + `superblocksBaseUrl`), similar to `AWS_PROFILE` + separate credential files. The CLI auth is **independent** of MCP auth — the CLI's `SUPERBLOCKS_AUTH_FILE` covers `superblocks pull / upload / dev`; MCP uses the `url` + Bearer entry in your AI client's config. The repository `.env` keeps the two CLI auth-file paths (`SUPERBLOCKS_CLOUD_AUTH_FILE` and `SUPERBLOCKS_CLOUD_PREM_AUTH_FILE`) so the `sb_auth_profile` helper can find them.

Suggested layout:

```text
~/.superblocks/profiles/cloud/auth.json
~/.superblocks/profiles/cloud_prem/auth.json
```

### Cloud (SaaS) profile

1. `export SUPERBLOCKS_AUTH_FILE="$HOME/.superblocks/profiles/cloud/auth.json"`
2. `mkdir -p "$(dirname "$SUPERBLOCKS_AUTH_FILE")"`
3. `superblocks config set domain app.superblocks.com` (or `eu.superblocks.com`, etc.)
4. `superblocks login` (or `superblocks login -t '<api-key>'`)

### Cloud Prem profile

1. `export SUPERBLOCKS_AUTH_FILE="$HOME/.superblocks/profiles/cloud_prem/auth.json"`
2. `superblocks config set domain <company>.superblocks.com` (hostname only, no `https://`; `<company>` is your Cloud Prem tenant slug)
3. `superblocks login` with a user API key for that instance

**Shell helper:** from this repository root, `source scripts/sb-auth-profile.sh` then `sb_auth_profile cloud` / `sb_auth_profile cloud_prem`. Use `sb_auth_profile list` (or `ls`) to print profile directories, whether `auth.json` exists, and which path is active in the current shell. (The helper must be **sourced**, not executed with `./scripts/...`, or `SUPERBLOCKS_AUTH_FILE` will not stick; the script avoids `set -e` at file scope so sourcing does not arm `errexit` in your interactive shell.)

### Optional: single MCP + `mcp-auth.json`

If the user only has **one** MCP server, they may rely on `~/.superblocks/mcp-auth.json` and multiple `base_url` entries (`superblocks-mcp auth add` / `auth import-current`). This skill assumes **two MCPs** unless the user says otherwise.

## 2) Pull from Cloud

1. `sb_auth_profile cloud` (or `export SUPERBLOCKS_AUTH_FILE=.../cloud/auth.json`)
2. From the git repo or empty folder where the app should live: `superblocks init '<cloud-application-url>'` **or**, for an existing project, `superblocks pull [apps/<path>]`
3. Confirm `.superblocks/superblocks.json` exists under the app path and reflects the **Cloud** application `id`. Note the **directory layout** of the pulled app — whether the app root has a `client/` + `server/` split or not — because §3a uses that layout (not `application_mode`) to determine 2.0 vs 3.0.

## 3) Prepare the Cloud Prem target application

### 3a) Confirm the Cloud Prem target is the right app type (blocking)

Superblocks has two application types — **2.0 / Application** (classic) and **3.0 / Fullstack App** — and they use **different on-disk code layouts**. The empty target app on Cloud Prem must be created as the type that matches the Cloud source, or `superblocks upload` will fail.

> ⚠️ **Do not** read **`application_mode`** from **`get_application_structure`** to make this determination. It currently returns **`2.0`** for **both** classic Applications and Fullstack Apps, so it cannot distinguish the two. The reliable signal is the **directory layout** of the source you just pulled in §2.

#### Step 1 — Detect the source app type by inspecting the pulled directory

After `superblocks pull` (section 2), inspect the local app directory (the one whose `.superblocks/superblocks.json` you confirmed in §2 step 3). Look at the top-level contents:

- **3.0 / Fullstack App** — the app root has both a **`client/`** subdirectory and a **`server/`** subdirectory. `client/` holds frontend code; `server/` holds backend APIs / integrations.
- **2.0 / Application (classic)** — the app root does **not** have a `client` / `server` split. Logic lives in nested files like `api.yaml`, page YAMLs, etc., directly under the app directory.

Use any read-only filesystem tool (`ls`, `find`, the agent's directory-list tool) to enumerate the app root. Tell the user what you found and the type you've concluded:

> The pulled app `apps/<name>` contains `client/` and `server/` at the top level → this is a **3.0 / Fullstack App**.

or

> The pulled app `apps/<name>` does not have `client/` + `server/` at the top level (top-level entries: `api.yaml`, `pages/`, `.superblocks/`) → this is a **2.0 / Application (classic)**.

#### Step 2 — Tell the user which Cloud Prem app type to create

Based on the detected source type, the user must create the matching empty target in the Cloud Prem UI:

| Detected source type (from §3a step 1) | On Cloud Prem: in the UI, go to the **Apps** page and use |
|-----------------------------------------|------------------------------------------------------------|
| **3.0 / Fullstack App** (has `client/` + `server/`) | **Create → Fullstack App** |
| **2.0 / Application (classic)** (no client/server split) | **Create → Application** |

After they create the correct app type, have them open it and copy the new **application UUID** from the URL (`/applications/<uuid>/...`). They'll need it for rebind in §3b.

#### Step 3 — (Recommended) Sanity-check the Cloud Prem template by pulling it

Because `application_mode` cannot confirm the type, the only positive verification is to `superblocks pull` the empty Cloud Prem app into a **sidecar** directory and check that its layout matches the source. Offer this to the user:

```bash
# Pull the empty Cloud Prem template into a sidecar directory (don't conflate with the migration target)
mkdir -p .cloud-prem-template-check
cd .cloud-prem-template-check
sb_auth_profile cloud_prem
superblocks init '<cloud-prem-empty-app-url>'   # or superblocks pull '<cloud-prem-empty-app-url>'
cd -

# Compare layouts
ls apps/<migration-target>/                          # source
ls .cloud-prem-template-check/<empty-app-name>/      # Cloud Prem template
```

Verify:

- If source has `client/` + `server/`, the Cloud Prem template **must** also have `client/` + `server/`.
- If source does **not** have that split, the Cloud Prem template **must not** have it either.

If the layouts disagree, the user picked the wrong "Create → …" option on Cloud Prem — have them delete the empty Cloud Prem app and recreate it as the matching type. Then re-run the sidecar pull and re-compare.

Once layouts match, **delete the sidecar directory** so it doesn't get confused with the actual migration target. Continue to §3b.

### 3b) Rebind and target app

On the Cloud Prem instance, **create** (or pick) the target application that will receive this code — **after** confirming the type matches via the directory-layout check in §3a. Copy its **application UUID** from the URL (`/applications/<uuid>/...`).

**Rebind the local project to Cloud Prem** before **`superblocks upload`** / **`superblocks dev`**:

- Set the `id` field in **`.superblocks/superblocks.json`** (inside the app directory) to the **Cloud Prem** application UUID.
- If the repo uses a monorepo root `.superblocks/superblocks.json` with a `resources` map, ensure the resource entry for this app still points at the correct **relative `location`**; only the per-app `id` must match the Cloud Prem app.

Do **not** upload to Cloud Prem while `id` still references the SaaS app.

## 4) Discover integration IDs in local code

Integration references in exported APIs typically appear as **`integration: <uuid>`** under `blocks[].step` in **`api.yaml`** files. Literal **`javascript`** / **`python`** steps are **not** integrations; do not remap those.

As the agent:

1. Enumerate all `**/api.yaml` (and any workflow/job YAML your tree uses) under the pulled app.
2. Collect every value of `integration:` that matches a **UUID** (RFC 4122 pattern). De-duplicate into a **backlog** of Cloud-side UUIDs still present in files. You will shrink this backlog **one UUID at a time** via **§5** / **§6** (no need to resolve every UUID before editing any file).
3. Optionally use MCP **`find_apps_by_integration`** on the **Cloud** MCP with the **Cloud** `base_url` to confirm usage and human-readable context.

## 5) Map Cloud integrations to Cloud Prem (existing + missing) — **one at a time**

Work **one Cloud integration UUID at a time**, end to end: for the UUID you are on, agree the Cloud Prem target (existing integration or **`create_integration`** per **§5c**), then **rewrite all occurrences of that Cloud UUID** in the app to the Cloud Prem UUID (**§6**) before starting the next UUID. The user does **not** need to provide or sign off on a **complete mapping table** before you begin; only the **current** pairing must be confirmed (or created) before you apply file edits for that UUID.

### 5a) Inventory on both sides (as needed, not “table first”)

Use **`list_integrations`** when you need to show the user choices or match by name / `plugin_id` / **kind** — not as a prerequisite to producing a full grid of every mapping.

1. On the **Cloud Prem** MCP: call **`list_integrations`** with the Cloud Prem **`base_url`**. Paginate with `limit` / `offset` if needed.
2. On the **Cloud** MCP: call **`list_integrations`** with the Cloud **`base_url`** (and use **`get_integration`** on the **Cloud** MCP for the **specific** Cloud UUID you are working on when you need full detail for naming or recreation).

### 5b) Existing Cloud Prem integrations (manual mapping, one UUID per turn)

Pick **one** Cloud integration UUID from the backlog. With the user, confirm which **existing** Cloud Prem integration it maps to (match by name, `plugin_id`, `kind`, and purpose — use **§5a** listings as needed). Once the user agrees **`cloud_uuid` → `cloud_prem_uuid`** for **that UUID only**, go to **§6** and replace **only** that Cloud UUID in the codebase. Then remove it from the backlog and continue with the next Cloud UUID.

**Do not guess** mappings for production data sources; wrong IDs will break APIs at runtime.

### 5c) Mandatory: missing integrations on Cloud Prem

Some Cloud integrations may have **no** suitable integration on Cloud Prem yet. The agent **may** create them on Cloud Prem using MCP **`create_integration`** (on the **Cloud Prem** MCP with the Cloud Prem **`base_url`**), **set placeholder secrets/configuration**, **remap** the new Cloud Prem integration id into the app (see **§6**), then **tell the user** they must **update real credentials** on Cloud Prem — **only** if **all** of the following are satisfied.

#### Hard requirements (non-negotiable)

1. **One integration at a time (create + remap).** For Cloud UUIDs that have **no** suitable Cloud Prem integration yet, process them **sequentially** — never “batch create” or remap multiple new integrations in one step without stepping through this checklist **per Cloud UUID**.

2. **You MUST prompt and confirm separately for every missing integration.** For **each** Cloud integration UUID that needs a **new** Cloud Prem integration, **stop** and ask the user an explicit question that names that integration (use Cloud **`get_integration`** / **`list_integrations`** metadata: name, `plugin_id`, `kind`, id).  
   - **Forbidden:** a single prompt such as “Create all missing integrations?” or “Approve the list below?” that covers more than one create.  
   - **Required:** after the user answers for integration A, only then move to integration B and **ask again from scratch** for B.

3. **Default is “do not create.”** If the user does not clearly confirm **for that specific UUID** that they want a **new** integration created on Cloud Prem, **do not** call **`create_integration`**. Leave that UUID unmapped in files for now and surface it as a blocker, or ask the user to map it to an existing Cloud Prem integration instead (**§5b**).

4. **Confirmation content.** Each per-integration prompt MUST include at least: Cloud integration **name**, **UUID**, `plugin_id` / **kind**, and a plain-language description that you will **create the integration on Cloud Prem using placeholder configuration**, **remap** the app’s YAML to the new Cloud Prem integration id, and that they will need to **open Integrations on Cloud Prem afterward and replace placeholders with real credentials**. Do **not** copy **real** secret values out of Cloud (from UI, API, or MCP responses) into chat, git, or the create payload.

5. **Never** call **`create_integration`** for a UUID the user skipped, declined, or has not yet answered.

#### After the user approves that Cloud UUID (ordered workflow)

Use Cloud **`get_integration`** (and public plugin docs if needed) **only** to learn **shape** (auth type, required fields, `urlBase`, etc.) — not to exfiltrate secrets.

1. **`create_integration` on Cloud Prem** — Build the payload the integrations API expects for that `plugin_id` / kind. For every secret or sensitive field, use an obvious **placeholder** string (for example `PLACEHOLDER_UPDATE_IN_CLOUD_PREM_UI`) or a minimal syntactically valid dummy value so the create succeeds. Use safe non-production hostnames/URLs where the schema requires them (for example public vendor API base URLs), not the customer’s real connection strings unless the user explicitly supplied them for this step. **Always set `enabledForV2: true` in the payload** — Cloud Prem integrations need this flag to be usable inside V2 / Code Mode applications, which is where the migrated app will reference them. If the Cloud-side `get_integration` returns `enabledForV2: false`, override to `true` on the Cloud Prem create; do not carry the old value over.

2. **Record the new Cloud Prem integration `id`** returned by **`create_integration`** — you now have the single pairing **`cloud_uuid` → `cloud_prem_uuid`** for **this** integration only (no need for a document listing every other UUID).

3. **Remap in the app (§6)** — Immediately apply **whole-token** YAML replacements for **this** Cloud UUID → new Cloud Prem UUID across the scanned app files (same rules as §6). Do not leave the old Cloud id in code for integrations you just created.

4. **Tell the user (required handoff)** — After create + remap (and optionally after **`superblocks upload`**), state clearly that:
   - The new integration on Cloud Prem currently has **placeholder credentials / configuration**;
   - They **must** open their **Cloud Prem** instance, go to **Integrations** (or the integration’s edit screen), find the integration **by name**, and **enter real secrets and configuration** before APIs that call that integration will work;
   - They should **rotate** any API keys or tokens that were ever pasted into chat if exposure is a concern.

5. **Optional credential refresh** — If placeholders are insufficient for later tests, the user may use MCP **`update_integration`** on the **Cloud Prem** MCP (with Cloud Prem `base_url`) **only** with values they supply; do not invent production secrets.

Repeat steps **1–4** for the next Cloud UUID only after the user has confirmed that integration in a **new** prompt (per hard requirements above).

## 6) Rewrite local files (per integration, same step as §5)

Apply file edits **one `cloud_uuid` → `cloud_prem_uuid` pair at a time**, whenever that pair is agreed (**§5b**) or returned from **`create_integration`** (**§5c**). Do **not** wait until every integration in the app has a Cloud Prem id.

1. For the **current** pairing only, apply **whole-token** replacements (`cloud_uuid` → `cloud_prem_uuid`) across the scanned app files (`**/api.yaml` and any other paths from **§4**). Prefer whole-token replacement so partial UUID matches do not corrupt data.
2. Re-scan the backlog from **§4**: confirm that Cloud UUID no longer appears as an `integration:` value (unless intentionally left for a later decision). Proceed to the next Cloud UUID in **§5**.
3. When **§5c** was used for that UUID, remind the user (if not already done there) that the new integration has **placeholders** and **must** be updated with real credentials on Cloud Prem before production use.

You may **`superblocks upload`** after each integration is remapped or after several — whatever fits the user’s workflow — as long as **§3** rebinding is correct and the CLI accepts the operation. Remaining unremapped Cloud UUIDs in files will keep failing against Cloud Prem until addressed.

## 7) Upload to Cloud Prem, then debug with Clark

This migration flow uses **`superblocks upload`** (one-shot push) or **`superblocks dev --upload-first`** (upload + local dev server) rather than **`superblocks push`** (git-backed commit flow). Direct the user to one of the two upload commands; either is acceptable, with the fallback pattern below.

> ⚠️ **The two upload commands behave inconsistently on first run.** After §3 rebind + §5/§6 integration remap, **either** command can sometimes leave the app **not rendering** when the user first opens it on Cloud Prem (the entities are uploaded but the client bundle doesn't load, or vice versa). **The reliable workaround is: try one command, refresh the Cloud Prem UI, and if the app doesn't render fall back to the OTHER command** and refresh again. The two commands fail in different ways, so the alternate almost always resolves it.

### Step-by-step

1. **Switch the CLI to Cloud Prem auth.**
    ```bash
    sb_auth_profile cloud_prem
    # (or: export SUPERBLOCKS_AUTH_FILE=... pointing at the Cloud Prem auth.json from §1)
    ```

2. **Pick an upload path** — either Option A or Option B. Both are valid; Option A is simpler, Option B keeps a local dev server running for follow-up edits.

    **Option A — `superblocks upload` (one-shot push, recommended starting point):**

    ```bash
    superblocks upload [apps/<path>]
    # example:
    # superblocks upload apps/cloud_prem_intake
    ```

    Resolve CLI errors using the CLI's messages (auth, signing, resource path).

    **Option B — `superblocks dev --upload-first` (upload + local dev server):**

    `dev --upload-first` builds the client bundle locally before pushing, so its **node_modules must be installed first** — otherwise the bundle build is incomplete and the app frequently won't render on Cloud Prem after the push:

    ```bash
    # 1. Install client deps (required for the dev server's bundle build).
    cd apps/<path>
    npm install              # if there's a package.json (most 3.0 / Fullstack apps)
    cd -

    # 2. Run dev with upload-first.
    superblocks dev --upload-first [apps/<path>]
    ```

    If the app directory has no `package.json` (typical for some 2.0 / classic apps), skip the `npm install` step and run `superblocks dev --upload-first` directly.
    Modify the `server/index.ts` by adding a single space to the comment at the end of the first line, to force the dev server to upload the file again.

3. **Verify the app renders on Cloud Prem.** Have the user open the app in the Cloud Prem UI and **hard-refresh** the page (Cmd-Shift-R / Ctrl-Shift-R).

    - **App renders normally →** continue to step 4 (smoke test).
    - **App does NOT render after refresh →** stop the current command (Ctrl-C the dev server if Option B is running), then run the **OTHER** upload path from step 2:
        - If you ran Option A, switch to Option B (remember `npm install` first).
        - If you ran Option B, switch to Option A.

        Refresh the Cloud Prem UI again. This A↔B fallback is the most common "the app isn't showing up" recovery — try it before reaching for deeper debugging.

4. **Smoke test.** Run a few APIs / workflows that hit integrations to confirm everything is wired up. If a runtime error shows up here, that's normally an integration-credential issue (§5c placeholders not yet replaced) rather than an upload-state issue.

5. **Clark:** Tell the user to open **Clark** (the Superblocks in-product assistant) while running the app and **ask Clark to debug and fix** runtime issues, API errors, UI breakages, and integration failures (for example after swapping integration ids or before real credentials are in place). Clark is the primary loop for fixing app behavior post-migration; this agent (Claude Code) complements that by editing repo files and re-running the upload command as needed.

6. **Iterating after the migration upload.** Once steps 3–5 are healthy, the user can switch to their normal dev loop. The A↔B fallback is specific to the **first post-migration upload**; once the app renders cleanly on Cloud Prem, ongoing edits + `superblocks dev` typically don't hit the same render issue.

For teams that **require** git commits and branch-based deploys, `superblocks push` may still apply outside this skill; this skill's default handoff is **upload → render check (A↔B fallback if needed) → smoke test → Clark**.

## 8) Post-migration checks

- In the Cloud Prem UI: open the app, run a few APIs / workflows that hit integrations. If the app stops rendering after a code change, re-run your chosen upload command from §7 — if that doesn't recover, try the **alternate** command (the A↔B fallback in §7 step 3) before reaching for deeper debugging.
- **Clark:** encourage the user to keep using **Clark** to chase down remaining defects until the app behaves as expected on Cloud Prem.
- **Integrations:** confirm the user has **replaced placeholder credentials** on Cloud Prem for every integration that was **`create_integration`**’d during migration (**§5c**); failures here often look like auth or connection errors at runtime.
- On the **Cloud Prem** MCP: **`get_application_summary`** / **`get_application_structure`** with the Cloud Prem `base_url` can confirm the app exists and surface its tree. (Do **not** use `application_mode` from that response as a type check — see §3a.)

## Agent guardrails

- Never print or commit API keys; `auth.json` and `mcp-auth.json` must stay out of git (`.gitignore`).
- Treat **`base_url`** normalization as exact: use the same scheme/host the customer uses (trailing slash as accepted by MCP tools).
- Always verify the **Cloud Prem app type** by **on-disk directory layout** (per **§3a**) before telling the user that rebinding / **`superblocks upload`** is safe — 3.0 / Fullstack App has `client/` + `server/` at the app root; 2.0 / Application does not. **Never** rely on `application_mode` from `get_application_structure` for this: it returns `2.0` for both types.
- **`create_integration`** creates real org resources — treat it as **high impact**. Prefer **placeholder** secrets/config for the initial create (**§5c**), then require the user to **edit the integration on Cloud Prem** with real credentials; the **per-integration** confirmation rules in **§5c** override any general efficiency goal.
- **Every `create_integration` payload must set `enabledForV2: true`** so the new Cloud Prem integration is usable from V2 / Code Mode app YAML. Do not omit it and do not propagate `enabledForV2: false` from the Cloud-side response.
- **Integration remapping:** update **`integration:`** UUIDs **one Cloud UUID at a time**; do not require the user to pre-approve a **full mapping table** before editing files (**§5** / **§6**).
- **Code mode (2.0)** apps may store logic outside classic `api.yaml`; if the tree uses additional config files, extend the scan beyond `api.yaml` when you find integration-like UUIDs there.

## When this skill does not apply

- Migrating **only** integrations without moving app code — use the dedicated [cloud-to-cloud-prem-integration-migration](../cloud-to-cloud-prem-integration-migration/SKILL.md) skill. That skill uses a CSV-based selection model (the user marks rows with `migrate_to_cloud_prem=y` upfront, same as the Phase 1 user/group/membership CSVs) and then iterates the candidate set autonomously — it does **not** prompt per integration the way §5c here does, because the CSV selection is treated as the consent signal.
- **Server-side secrets** or agent deployment topology (Helm, EKS, etc.) — use the `dedicated-deployment` and `cloud-prem-deployments` repositories and their runbooks, not this app-migration skill.
