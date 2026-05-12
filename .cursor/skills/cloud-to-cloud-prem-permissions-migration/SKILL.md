---
name: cloud-to-cloud-prem-permissions-migration
description: >-
  Reconciles resource-level RBAC on Cloud Prem (applications, integrations, folders, workflows,
  jobs, and related org resources) with access that existed in Superblocks Cloud, using the two
  Superblocks MCP servers. Assumes users and groups already exist on Cloud Prem and that target apps and
  integrations have been migrated. Uses get_application_access, list_rbac_assignments, and
  grant_resource_access / update_resource_access / revoke_resource_access with Cloud Prem principal_id
  and Cloud Prem resource IDs only. Use after cloud-to-cloud-prem-users-groups-migration and
  cloud-to-cloud-prem-app-migration when sharing migrated assets with the same principals who had
  access in Cloud.
disable-model-invocation: true
---

# Cloud → Cloud Prem permissions (resource access) migration

This workflow configures **what users and groups can do** on **Cloud Prem** org resources—primarily **applications** and **integrations**, and as needed **folders**, **workflows**, **jobs**, **knowledge**, and other RBAC-backed resources—so it matches what they had in **Superblocks Cloud**.

**Prerequisites**

1. **Users and groups** exist on Cloud Prem and duplicate identities have been reconciled ([cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md)).
2. **Target applications and integrations** exist on Cloud Prem with correct `application_mode` and remapped integration IDs ([cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)).

**Overall migration order (this repo)**

1. Migrate users and groups.
2. Migrate target applications and their integrations.
3. **This skill:** update permissions so migrated resources are shared with the principals who had access in Cloud.

## MCP routing (critical)

| Environment | Example `base_url` | MCP server process |
|-------------|-------------------|---------------------|
| Superblocks Cloud (SaaS) | `https://app.superblocks.com/` (or regional SaaS URL) | **Cloud** MCP |
| Cloud Prem | `https://<company>.superblocks.com/` | **Cloud Prem** MCP |

Each MCP process is authenticated to **one** `superblocksBaseUrl`. Pass the matching `base_url` on every tool call and invoke the tool from the **matching** server. If the user’s Cursor `mcpServers` keys differ, follow **their** names.

## Principal and resource ID rules

- **`principal_id` on Cloud Prem** must always be the **Cloud Prem** user or group UUID. **Never** pass a Cloud UUID as `principal_id` on Cloud Prem.
- **Resource IDs** (application id, integration id, folder id, etc.) on Cloud Prem **differ** from Cloud. After app and integration migration, use **Cloud Prem** resource identifiers from **`list_applications`**, **`get_application_summary`**, **`list_integrations`**, **`get_integration`**, or the customer’s migrated-app list—not the old Cloud ids.
- **Map Cloud principals → Cloud Prem principals** using the same normalized email (users) and normalized group name (groups) rules as the users/groups skill; refresh **`list_users`** on Cloud Prem if the customer added users since the last inventory.

## Recommended sequence

1. **Source of truth (Cloud):** for each app that has a Cloud Prem counterpart (and branch if relevant), **`get_application_access`** on **Cloud** with **Cloud** `application_id` / `app_url`. Where access is scoped to other resource types, call **`list_rbac_assignments`** on **Cloud** with **Cloud** `resource_type` and **Cloud** `resource_id`. Build a logical table: which **Cloud Prem** resource (after id mapping) should receive which **Cloud Prem** principal and which role, mirroring Cloud.
2. **Current state (Cloud Prem):** **`get_application_access`** and **`list_rbac_assignments`** on **Cloud Prem** for the same resources to avoid blind duplicate grants and to plan revokes if the user wants a full mirror.
3. **Apply on Cloud Prem only:** **`grant_resource_access`**, **`update_resource_access`**, **`revoke_resource_access`** with Cloud Prem **`principal_id`** and Cloud Prem resource identifiers.
4. **Verify:** re-read **`get_application_access`** / **`list_rbac_assignments`** on Cloud Prem; resolve mismatches with the user.

## Optional: org-wide roles

If the customer also wants **organization roles** (not per-app) aligned with Cloud and that was not done during the users/groups phase: **`list_roles`** with `type=org` on Cloud Prem, then **`update_user_org_role`** / **`update_group_org_role`** or **`delete_group_org_role`**. Still use only Cloud Prem principal UUIDs.

## MCP capability boundary

If a required grant type or resource type is missing from the MCP descriptor set, produce a **gap list** for the Admin UI and stop rather than guessing.

## Related

- Principals first: [cloud-to-cloud-prem-users-groups-migration](../cloud-to-cloud-prem-users-groups-migration/SKILL.md)
- Apps + integrations (app-driven): [cloud-to-cloud-prem-app-migration](../cloud-to-cloud-prem-app-migration/SKILL.md)
- Integrations only (no app code): [cloud-to-cloud-prem-integration-migration](../cloud-to-cloud-prem-integration-migration/SKILL.md)
- Repo MCP setup: `README.md` at repository root
