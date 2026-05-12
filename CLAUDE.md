# Cloud Prem app migrations — project context for Claude Code

Auto-loaded when Claude Code is run with this repo in the working directory. Mirrors
`.cursor/rules/cloud-prem-terminology.mdc` so the same conventions apply regardless of
which AI tool the user is driving the migration with.

## Cloud Prem wording (terminology)

In **all user-facing text** you author for this repository — README, skills, comments in
migration docs, commit messages, and chat replies — refer to the self-hosted Superblocks
enterprise deployment by its full name: **Cloud Prem**.

**Do not** shorten "Cloud Prem" to **"Prem"** in prose. Avoid phrases like "on Prem",
"to Prem", "vs Prem", "from Prem", "Prem SCIM", "Prem MCP", "Prem deployment", or
"Cloud vs Prem" when the meaning is the Cloud Prem product or host.

**Prefer**

- "on Cloud Prem", "to Cloud Prem", "Cloud and Cloud Prem", "Cloud vs Cloud Prem"
- "Cloud Prem base URL", "Cloud Prem org", "Cloud Prem SCIM token", "Cloud Prem MCP server"

**Exceptions (identifiers only — do not expand these in code)**

- CLI flags and env vars for this repo's scripts use names such as **`--cloud-prem-auth`**,
  **`SUPERBLOCKS_SCIM_TOKEN_CLOUD_PREM`** (spell "Cloud Prem" in help text, not "Prem" alone).
- MCP server keys like `superblocks-cloud-prem`.
- Path segments or profile folder names such as `profiles/cloud_prem` when matching this
  repo's `sb_auth_profile` naming.

When in doubt, spell out **Cloud Prem**.

## Where to find the workflow

- **README.md** at the repo root — primary user-facing reference for the migration
  workflow (Phase 1 SCIM scripts, Phase 2 app migration, Phase 3 permissions). Always defer
  to its commands and step ordering.
- **`.claude/skills/`** — agent skills (one master + three narrower for Phase 1, plus
  Phase 2 and Phase 3). Attach the right one explicitly when the user asks for help with
  a specific phase.
- **`.cursor/skills/`** — equivalent skills for Cursor users; do not edit unless asked.
  When updating a `.claude/` skill, also update its `.cursor/` twin so both stay in sync.
- **`.env`** at the repo root — gitignored shared config (SCIM tokens, base URLs, CLI
  auth-file paths). Never print token values back to the user.
