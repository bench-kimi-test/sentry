The Seer→Sentry DB migration for Seer project settings is a precursor to this project [Tech Spec: Migrate Seer Settings to Sentry Database](https://www.notion.so/Tech-Spec-Migrate-Seer-Settings-to-Sentry-Database-3208b10e4b5d80f58ea0d7b77a301e2a?pvs=21)

## Status

Todo

## Authors

@Sofia Rest

## Motivation

The current Seer configuration APIs were built incrementally across multiple teams. Now that all Seer data lives in Sentry's DB (ProjectOption + SeerProjectRepository + RepositorySettings), we have an opportunity to consolidate these into focused endpoints with consistent patterns.

### Current problems

1. **No pagination on repo lists** — `ProjectSeerPreferencesEndpoint` returns the full connected repos and code mapping repos lists unpaginated. `OrganizationRepositorySettingsEndpoint` returns the full updated repo list unpaginated.
2. **Limited sorting and filtering** — `OrganizationAutofixAutomationSettingsEndpoint` hardcodes sort by slug with only a name/slug substring search. `OrganizationRepositoriesEndpoint` hardcodes sort by name and supports `query`, `integration_id`, and `status` filters but cannot filter by code review status. Neither supports configurable sort fields or structured filtering.
3. **Mixed concerns** — `ProjectSeerPreferencesEndpoint` mixes core settings (stopping point, handoff) with repository management (repo list, code mapping repos) in a single blob. `autofixAutomationTuning`, `seerScannerAutomation`, and `seerNightshiftTweaks` are writable only via the general `ProjectDetailsEndpoint` PUT, not through any Seer-specific endpoint.
4. **Repos identified by tuple instead of ID** — the API accepts `(provider, externalId, owner, name)` tuples, requiring the backend to look up each `Repository` row. The frontend already has `repo.id` available but throws it away to construct the tuple.
5. **No individual-item endpoints** — updating a single project's settings requires the bulk endpoint. Adding/removing a single repo requires replacing the entire list.
6. **Missing features on bulk endpoint** — `seerScannerAutomation` and `seerNightshiftTweaks` cannot be set in bulk.

Together these issues slow down feature development — every new Seer setting requires changes across multiple endpoints and frontend hooks — while also degrading user experience through unnecessary over-fetching, limited bulk operations, and no way to filter or sort large lists.

---

## Endpoint Design

Break the current monolithic `ProjectSeerPreferencesEndpoint` and scattered `ProjectDetailsEndpoint` fields into focused endpoints:

| Area               | New Endpoint                                                                          | What it manages                                                                                       |
| ------------------ | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| Project settings   | `GET/PUT /organizations/{org}/seer/projects/[{project}/]`                             | Paginated list + bulk update, or single-project settings (agent, stopping point, scanner, nightshift) |
| Connected repos    | `GET/POST/PUT/DELETE /organizations/{org}/seer/projects/{project}/repos/[{repo_id}/]` | Per-project `SeerProjectRepository` CRUD, list or individual operations                               |
| Code mapping repos | `GET /organizations/{org}/seer/projects/{project}/code-mapping-repos/`                | Per-project read-only derived repos from `RepositoryProjectPathConfig`                                |

Plus standalone endpoints for code review settings:

| Area             | New Endpoint                                           | What it manages                                              |
| ---------------- | ------------------------------------------------------ | ------------------------------------------------------------ |
| Repo code review | `GET/PUT /organizations/{org}/seer/repos/[{repo_id}/]` | Paginated repo list + bulk update, or single-repo operations |

### Key improvements

- **Token-based query language** — GET and PUT share the same `query` syntax with structured filters (eg, `agent:none`, `reposCount>:0`, `id:[1,2,3]`). Bulk updates can target the same filter you previewed in GET.
- **Repos identified by ID, not tuple** — POST uses `repositoryId` (Sentry `Repository.id`) instead of `(provider, externalId, owner, name)`. The frontend already has `repo.id`; the tuple was error-prone and required a backend lookup step.
- **Pagination, sorting, and filtering** — all list endpoints support cursor-based pagination, configurable `sortBy`, and structured filtering.
- **Individual-item endpoints** — per-project settings GET/PUT, per-repo GET/PUT/DELETE. No more replacing the entire list to change one repo's branch.
- **High-level API contract** — the frontend sends `agent`, `stoppingPoint`, `integrationId` instead of raw storage fields (`autofixAutomationTuning`, `automationHandoff`, `automated_run_stopping_point`). The backend translates to/from storage. The frontend no longer needs to know how handoff options map to agent types.
- **All Seer settings in one place** — `seerScannerAutomation`, `seerNightshiftTweaks`, agent config, stopping point, and handoff are all readable and writable through dedicated Seer endpoints, with bulk support. Currently scattered across `ProjectDetailsEndpoint`, `ProjectSeerPreferencesEndpoint`, and `OrganizationAutofixAutomationSettingsEndpoint`.
- **Validated nightshift writes** — `seerNightshiftTweaks` gets a proper nested serializer instead of the current unvalidated `JSONField`.
- **Settings separated from repos** — core settings and connected repos are independent endpoints. Updating a setting doesn't require sending the repo list, and vice versa.

### What we're NOT changing

- **Organization-level Seer settings** — already fully readable and writable via `GET/PUT /organizations/{org}/`. Nine org-level options are handled there. No new endpoint needed.
- **Coding agents endpoint** — `/organizations/{org}/integrations/coding-agents/` manages integration discovery, a separate concern from configuration.
- **Other `/seer/` endpoints** — `seer/onboarding-check/`, `seer/setup-check/`, `seer/workflows/`, `seer/explorer-*`, `seer/night-shift/`, `seer/models/` are unaffected.

### Permissions

**Project-scoped endpoints** (per-project settings, connected repos, code mapping repos) use `ProjectEventPermission` (`event:read`/`event:write`/`event:admin`). The current `ProjectSeerPreferencesEndpoint` was intentionally set to this instead of `ProjectPermission` (`project:write`) so that any org member who can view events can also configure Seer preferences — these settings are closer to "how should the AI handle my events" than "manage project infrastructure" ([#88004](https://github.com/getsentry/sentry/pull/88004)).

**Org-scoped bulk project endpoints** use `OrganizationPermission`, matching the current `OrganizationAutofixAutomationSettingsEndpoint`. Per-project authorization is handled internally by `self.get_projects()`.

**Org-scoped repo endpoints**: GET requires `org:read`, PUT requires `org:write` or `org:integrations`. The current `OrganizationRepositoriesEndpoint` (GET `/repos/`) uses `OrganizationIntegrationsLoosePermission` which allows `org:read` for all methods. The current `OrganizationRepositorySettingsEndpoint` (PUT `/repos/settings/`) uses `OrganizationIntegrationsPermission` which requires `org:write` or `org:integrations` for writes. We preserve both behaviors: the Seer automation UI renders a read-only view for users with only `org:read` (inputs disabled, warning banner via `useCanWriteSettings`), so GET must stay accessible to `org:read`. No existing permission class has this exact split — we may need a new one or method-level checks.

---

### Project Seer Settings

### `GET /organizations/{org}/seer/projects/`

Replaces `GET /organizations/{org}/autofix/automation-settings/`.

**Permission:** `OrganizationPermission`.

**Query parameters:**

| Parameter  | Type   | Description                                            |
| ---------- | ------ | ------------------------------------------------------ |
| `query`    | string | Filter using token syntax (see below)                  |
| `sortBy`   | string | One of: `name`, `agent`, `reposCount`. Default: `name` |
| `cursor`   | string | Pagination cursor                                      |
| `per_page` | int    | Results per page (default 25, max 100)                 |

**Query syntax:**

Both GET and PUT use the same `query` language. Bare text is a name/slug substring match. Tokens support structured filtering.

Comparison operators:

| Operator | Meaning               | Example           |
| -------- | --------------------- | ----------------- |
| `:`      | Equals                | `name:my-project` |
| `!:`     | Not equals            | `agent!:cursor`   |
| `>:`     | Greater than          | `reposCount>:0`   |
| `<:`     | Less than             | `reposCount<:5`   |
| `>=:`    | Greater than or equal | `reposCount>=:1`  |
| `<=:`    | Less than or equal    | `reposCount<=:10` |

Supported tokens:

| Token           | Value type  | Description                                                      |
| --------------- | ----------- | ---------------------------------------------------------------- |
| `id`            | int or list | Project ID. Supports `id:123` or `id:[1,2,3]` for multi-select.  |
| `name`          | string      | Project name or slug (case-insensitive contains)                 |
| `reposCount`    | int         | Number of connected repos. Supports all comparison operators.    |
| `agent`         | string      | One of: `seer`, `cursor`, `claude`, `none`                       |
| `stoppingPoint` | string      | One of: `off`, `root_cause` (feature-gated), `plan`, `create_pr` |

**Response shape:**

```json
[
  {
    "projectId": 123,
    "projectSlug": "my-project",
    "agent": "cursor",
    "integrationId": "12345",
    "stoppingPoint": "root_cause",
    "seerScannerAutomation": true,
    "seerNightshiftTweaks": {
      "enabled": true,
      "max_candidates": 50,
      "extra_triage_instructions": "Focus on auth errors",
      "intelligence_level": "high",
      "reasoning_effort": "high"
    },
    "reposCount": 2
  }
]
```

> **Note:** The response uses high-level fields (`agent`, `integrationId`, `stoppingPoint`) instead of the raw storage fields (`autofixAutomationTuning`, `automationHandoff`). The backend translates between the two. The frontend no longer needs to know the storage layout.

### `PUT /organizations/{org}/seer/projects/`

Replaces `POST /organizations/{org}/autofix/automation-settings/`.

**Permission:** `OrganizationPermission`.

**Request:**

`query` (required) targets which projects to update — same token syntax as GET:

```json
// Update all projects matching a filter
{
  "query": "agent:none reposCount>:0",
  "agent": "seer",
  "stoppingPoint": "create_pr"
}

// Update specific projects by ID
{
  "query": "id:[1,2,3]",
  "agent": "cursor",
  "integrationId": "12345",
  "stoppingPoint": "create_pr"
}
```

At least one **update field** must be provided:

| Field                   | Type           | Notes                                                                                    |
| ----------------------- | -------------- | ---------------------------------------------------------------------------------------- |
| `agent`                 | string         | One of: `none`, `seer`, `cursor`, `claude`.                                              |
| `integrationId`         | string         | Required when `agent` is `cursor` or `claude`. Must reference an active org integration. |
| `stoppingPoint`         | string         | One of: `off`, `root_cause` (feature-gated), `plan`, `create_pr`.                        |
| `seerScannerAutomation` | bool           | Whether Seer scanner is enabled.                                                         |
| `seerNightshiftTweaks`  | object or null | Nightshift configuration. Validated with nested serializer.                              |

**Backend translation** from high-level fields to storage:

| Input                          | Storage effect                                                                                     |
| ------------------------------ | -------------------------------------------------------------------------------------------------- |
| `agent: "none"`                | `autofixAutomationTuning = "off"`, clear handoff                                                   |
| `agent: "seer"`                | `autofixAutomationTuning = "medium"`, clear handoff                                                |
| `agent: "cursor"` / `"claude"` | `autofixAutomationTuning = "medium"`, set handoff (`target`, `integration_id`, `handoff_point`)    |
| `stoppingPoint: "off"`         | `autofixAutomationTuning = "off"`                                                                  |
| `stoppingPoint: "root_cause"`  | `automated_run_stopping_point = "root_cause"`. If handoff exists, sets `auto_create_pr = false`.   |
| `stoppingPoint: "plan"`        | `automated_run_stopping_point = "code_changes"`. If handoff exists, sets `auto_create_pr = false`. |
| `stoppingPoint: "create_pr"`   | `automated_run_stopping_point = "open_pr"`. If handoff exists, sets `auto_create_pr = true`.       |

**Changes from current `POST /autofix/automation-settings/`:**

- Method changes from POST to PUT
- Replaces `projectIds` with `query` for targeting (supports filters and `id:[1,2,3]` for explicit selection)
- Uses high-level `agent` / `integrationId` / `stoppingPoint` instead of raw `autofixAutomationTuning` / `automationHandoff`
- Adds `seerScannerAutomation` and `seerNightshiftTweaks` (currently only settable per-project)
- Removes `projectRepoMappings` and `appendRepositories` — repo management moves to dedicated connected repos endpoints

**Response:** `204 No Content`.

### `GET /organizations/{org}/seer/projects/{project}/`

New — replaces reading Seer fields from `GET /projects/{org}/{project}/` and `GET /projects/{org}/{project}/seer/preferences/`.

**Permission:** `ProjectEventPermission`.

**Response:** Same fields as bulk get, only a single object and not a list.

### `PUT /organizations/{org}/seer/projects/{project}/`

Replaces updating Seer fields via `PUT /projects/{org}/{project}/` (`autofixAutomationTuning`, `seerScannerAutomation`, `seerNightshiftTweaks`) and `POST /projects/{org}/{project}/seer/preferences/` (stopping point, handoff).

**Permission:** `ProjectEventPermission`.

**Request:** Same writable fields as bulk update, without `query`. At least one update field must be provided.

```json
{
  "agent": "cursor",
  "integrationId": "12345",
  "stoppingPoint": "create_pr"
}
```

**Response:** Updated project settings object (same shape as GET).

---

### Project Connected Repositories

### `GET /organizations/{org}/seer/projects/{project}/repos/`

Replaces reading `preference.repositories` from `GET /projects/{org}/{project}/seer/preferences/`.

**Permission:** `ProjectEventPermission`.

**Response shape:**

```json
[
  {
    "repositoryId": 456,
    "provider": "integrations:github",
    "owner": "getsentry",
    "name": "sentry",
    "externalId": "12345678",
    "integrationId": "789",
    "branchName": "main",
    "branchOverrides": [
      {"tagName": "environment", "tagValue": "staging", "branchName": "staging"}
    ],
    "instructions": "Follow the AGENTS.md conventions"
  }
]
```

**Query parameters:**

| Parameter  | Type   | Description                                 |
| ---------- | ------ | ------------------------------------------- |
| `query`    | string | Filter using token syntax (see below)       |
| `sortBy`   | string | One of: `name`, `provider`. Default: `name` |
| `cursor`   | string | Pagination cursor                           |
| `per_page` | int    | Results per page (default 25, max 100)      |

**Query syntax:**

Bare text is a name substring match (case-insensitive contains).

| Token      | Value type | Description                                 |
| ---------- | ---------- | ------------------------------------------- |
| `name`     | string     | Repository name (case-insensitive contains) |
| `provider` | string     | Provider ID (e.g. `integrations:github`)    |

### `POST /organizations/{org}/seer/projects/{project}/repos/`

Add repos to the project. Replaces the repo-setting portion of `POST /projects/{org}/{project}/seer/preferences/`.

**Permission:** `ProjectEventPermission`.

**Request:**

```json
{
  "repos": [
    {
      "repositoryId": 456,
      "branchName": "main",
      "instructions": "Follow AGENTS.md",
      "branchOverrides": []
    }
  ]
}
```

Repos are identified by `repositoryId` (Sentry `Repository.id`). The `Repository` must belong to the same organization.

**Why `repositoryId`?** The frontend already has `repo.id` from `/organizations/{org}/repos/` but currently throws it away to construct a `(provider, externalId, owner, name)` tuple. Using `repositoryId` directly eliminates the error-prone tuple construction and the backend lookup step.

**Response:** `201` with the newly added repo config objects.

### `PUT /organizations/{org}/seer/projects/{project}/repos/`

**Permission:** `ProjectEventPermission`.

Replace all repos for this project. Send empty array to clear.

**Request:** Same shape as POST.

**Implementation:** Delete existing `SeerProjectRepository` rows for this project, create new ones. Wrapped in a transaction.

**Response:** `200` with the full updated repo list.

### `GET /organizations/{org}/seer/projects/{project}/repos/{repo_id}/`

**Permission:** `ProjectEventPermission`.

Get a single connected repo by `Repository.id`. Looks up `SeerProjectRepository` via the `(project, repository)` unique constraint.

### `PUT /organizations/{org}/seer/projects/{project}/repos/{repo_id}/`

**Permission:** `ProjectEventPermission`.

Update `branchName`, `instructions`, and/or `branchOverrides` for one connected repo.

### `DELETE /organizations/{org}/seer/projects/{project}/repos/{repo_id}/`

**Permission:** `ProjectEventPermission`.

Remove a connected repo from the project. Returns `204 No Content`.

---

### Project Code Mapping Repos

### `GET /organizations/{org}/seer/projects/{project}/code-mapping-repos/`

Replaces reading `code_mapping_repos` from `GET /projects/{org}/{project}/seer/preferences/`.

**Permission:** `ProjectEventPermission`.

**Read-only.** These repos are derived on the fly from `RepositoryProjectPathConfig` (code mappings), not stored as `SeerProjectRepository` rows.

**Response shape:**

```json
[
  {
    "repositoryId": 789,
    "provider": "integrations:github",
    "owner": "getsentry",
    "name": "seer",
    "externalId": "87654321",
    "integrationId": "789"
  }
]
```

**Query parameters:**

| Parameter  | Type   | Description                                 |
| ---------- | ------ | ------------------------------------------- |
| `sortBy`   | string | One of: `name`, `provider`. Default: `name` |
| `cursor`   | string | Pagination cursor                           |
| `per_page` | int    | Results per page (default 25, max 100)      |

---

### Repository Code Review Settings

Code review settings are Seer-specific, so they belong under `/seer/repos/`. After migration, `/repos/settings/` is deleted and the `expand=settings` option on `/repos/` is removed (see Deprecation Plan).

**New endpoints:**

| Method | Path                                         | Description                                          |
| ------ | -------------------------------------------- | ---------------------------------------------------- |
| GET    | `/organizations/{org}/seer/repos/`           | Paginated repo list with code review settings inline |
| PUT    | `/organizations/{org}/seer/repos/`           | Bulk update code review settings                     |
| GET    | `/organizations/{org}/seer/repos/{repo_id}/` | Single repo with code review settings                |
| PUT    | `/organizations/{org}/seer/repos/{repo_id}/` | Update single repo's code review settings            |

### `GET /organizations/{org}/seer/repos/`

Replaces `GET /organizations/{org}/repos/?expand=settings`.

**Permission:** Requires `org:read`.

**Query parameters:**

| Parameter  | Type   | Description                                                |
| ---------- | ------ | ---------------------------------------------------------- |
| `query`    | string | Filter using token syntax (see below)                      |
| `sortBy`   | string | One of: `name`, `dateCreated`, `provider`. Default: `name` |
| `cursor`   | string | Pagination cursor                                          |
| `per_page` | int    | Results per page (default 25, max 100)                     |

**Query syntax:**

Both GET and PUT use the same `query` language. Bare text is a name substring match (case-insensitive contains).

| Token               | Value type  | Description                                                        |
| ------------------- | ----------- | ------------------------------------------------------------------ |
| `id`                | int or list | Repository ID. Supports `id:123` or `id:[1,2,3]` for multi-select. |
| `name`              | string      | Repository name (case-insensitive contains)                        |
| `enabledCodeReview` | bool        | One of: `true`, `false`                                            |
| `integrationId`     | string      | Filter by integration ID                                           |
| `status`            | string      | One of: `active` (default), `deleted`, `unmigratable`              |

Same comparison operators as project settings query syntax (`:`, `!:`, `>:`, `<:`, `>=:`, `<=:`).

**Response shape:**

```json
[
  {
    "id": "123",
    "name": "getsentry/sentry",
    "url": "<https://github.com/getsentry/sentry>",
    "provider": {"id": "integrations:github", "name": "GitHub"},
    "status": "active",
    "dateCreated": "2024-01-15T00:00:00Z",
    "integrationId": "456",
    "externalSlug": "getsentry/sentry",
    "externalId": "12345678",
    "settings": {
      "enabledCodeReview": true,
      "codeReviewTriggers": ["on_new_commit", "on_ready_for_review"]
    }
  }
]
```

> **Note:** The response shape matches the existing `GET /organizations/{org}/repos/?expand=settings` exactly. The only difference is that `settings` is always included — no `expand` parameter needed. This lets the frontend reuse the existing `RepositoryWithSettings` type as-is.

### `PUT /organizations/{org}/seer/repos/`

Replaces `PUT /organizations/{org}/repos/settings/`.

**Permission:** Requires `org:write` or `org:integrations`.

**Request:**

| Field                | Type     | Required | Description                                                   |
| -------------------- | -------- | -------- | ------------------------------------------------------------- |
| `query`              | string   | Yes      | Filter using token syntax (same as GET)                       |
| `enabledCodeReview`  | bool     | No\*     | Whether code review is enabled                                |
| `codeReviewTriggers` | string[] | No\*     | When code review runs: `on_new_commit`, `on_ready_for_review` |

- At least one update field is required.

```json
// Enable code review on all repos that don't have it yet
{
  "query": "enabledCodeReview:false",
  "enabledCodeReview": true,
  "codeReviewTriggers": ["on_new_commit", "on_ready_for_review"]
}

// Update specific repos by ID
{
  "query": "id:[1,2,3]",
  "enabledCodeReview": true
}
```

**Response:** Updated repo objects (same shape as GET list).

### `GET /organizations/{org}/seer/repos/{repo_id}/`

**Permission:** Requires `org:read`.

Single repo with settings always included. Same shape as one item in the list response.

### `PUT /organizations/{org}/seer/repos/{repo_id}/`

**Permission:** Requires `org:write` or `org:integrations`.

Update code review settings for a single repository.

**Request:**

```json
{
  "enabledCodeReview": true,
  "codeReviewTriggers": ["on_new_commit", "on_ready_for_review"]
}
```

**Response:** Updated repo object.

---

## Repo Identification

### Current state

The frontend sends repos as `(provider, externalId, owner, name)` tuples. The backend resolves these to `Repository.id` for storage.

### Target state

New endpoints accept `repositoryId` (Sentry `Repository.id`).

### Frontend migration

The frontend already fetches repos from `GET /organizations/{org}/repos/` which returns `Repository` objects with an `id` field. Currently it discards this and constructs the tuple:

```tsx
// Current (autofixRepositories.tsx)
const [owner, name] = (repo.name || '/').split('/');
let provider = repo.provider?.id || '';
if (provider?.startsWith('integrations:')) {
  provider = provider.split(':')[1]!;
}
const repoData = {
  provider,
  owner,
  name,
  external_id: repo.externalId,
  organization_id: parseInt(organization.id, 10),
  integration_id: repo.integrationId,
  branch_name: settings?.branch || '',
  instructions: settings?.instructions || '',
  branch_overrides: settings?.branch_overrides || [],
};
```

After migration:

```tsx
// New
const repoData = {
  repositoryId: parseInt(repo.id, 10),
  branchName: settings?.branch || '',
  instructions: settings?.instructions || '',
  branchOverrides: settings?.branchOverrides || [],
};
```

## Deprecation Plan

After the new endpoints are stable and the frontend is migrated:

1. **`ProjectSeerPreferencesEndpoint`** (`/projects/{org}/{project}/seer/preferences/`) — delete entirely. Settings move to `/seer/projects/{project}/`, repos to `/seer/projects/{project}/repos/` and `/seer/projects/{project}/code-mapping-repos/`.
2. **`OrganizationAutofixAutomationSettingsEndpoint`** (`/organizations/{org}/autofix/automation-settings/`) — delete entirely. Replaced by `/seer/projects/` list + bulk update.
3. **Seer fields on `ProjectDetailsEndpoint`** — `autofixAutomationTuning`, `seerScannerAutomation`, `seerNightshiftTweaks` writes should be deprecated on `PUT /projects/{org}/{project}/`. Reads can remain on the GET serializer for backwards compatibility. Frontend consumers that currently write these fields via `ProjectDetailsEndpoint` and need migration:
   - `static/app/utils/seer/stoppingPoint.ts` — writes `autofixAutomationTuning` via `fetchMutation` PUT
   - `static/app/utils/seer/preferredAgent.tsx` — writes `autofixAutomationTuning` via `fetchMutation` PUT
   - `static/app/utils/seer/useMutateAutofixProject.ts` — writes `autofixAutomationTuning` via `fetchMutation` PUT
   - `static/app/components/events/autofix/codingAgentIntegrationCta.tsx` — writes `autofixAutomationTuning`, `seerScannerAutomation` via `useUpdateProject`
   - `static/gsApp/views/seerAutomation/components/projectDetails/autofixAgent.tsx` — writes `seerNightshiftTweaks` via `useUpdateProject`
   - `static/gsApp/views/seerAutomation/components/projectDetails/nightShift.tsx` — writes `seerNightshiftTweaks` via `useUpdateProject`
   - `static/app/views/settings/projectSeer/index.tsx` — form fields with `saveOnBlur` (old UI, behind `!showNewSeer`)
4. **`projectRepoMappings` on `OrganizationAutofixAutomationSettingsEndpoint`** — the field exists in the backend serializer and the frontend type union but is never sent by any frontend code path. Delete the `projectRepoMappings` field from `SeerAutofixSettingsPostSerializer`, the `ProjectRepoMappingField` validator, the `merge_repositories` helper, and the corresponding `AutofixAutomationUpdate` type union variant in `useBulkAutofixAutomationSettings.ts`.
5. **`expand=settings` on `OrganizationRepositoriesEndpoint`** (`/organizations/{org}/repos/`) — no longer needed once frontend uses `/seer/repos/`. The `expand` option can be removed. Frontend consumers to migrate:
   - `static/app/components/repositories/useRepositoryWithSettings.tsx` — single-repo fetch with `expand=settings`
   - `static/gsApp/views/seerAutomation/hooks/useRepoDetailsDrawer.tsx` — calls `useRepositoryWithSettings`
   - `static/gsApp/views/seerAutomation/components/repoTable/seerRepoTable.tsx` — references query key
   - `static/gsApp/views/seerAutomation/components/repoTable/seerRepoTableRow.tsx` — references query key
   - `static/gsApp/views/seerAutomation/components/repoDetails/repoDetailsForm.tsx` — references query key
   - `static/app/components/repositories/scmIntegrationTree/useScmIntegrationTreeData.ts` — uses `organizationRepositoriesWithSettingsInfiniteOptions`
   - `static/gsApp/views/seerAutomation/onboarding/hooks/seerOnboardingContext.tsx` — uses `organizationRepositoriesWithSettingsInfiniteOptions`
6. **`OrganizationRepositorySettingsEndpoint`** (`/organizations/{org}/repos/settings/`) — delete entirely. Replaced by `PUT /seer/repos/` and `PUT /seer/repos/{repo_id}/`. Frontend consumers to migrate:
   - `static/app/components/repositories/useBulkUpdateRepositorySettings.tsx` — mutation hook for bulk PUT
   - `static/gsApp/views/seerAutomation/components/repoTable/seerRepoTable.tsx` — calls `useBulkUpdateRepositorySettings`
   - `static/gsApp/views/seerAutomation/components/repoTable/seerRepoTableHeader.tsx` — types against it
   - `static/gsApp/views/seerAutomation/components/repoTable/seerRepoTableRow.tsx` — types against it
   - `static/gsApp/views/seerAutomation/components/repoDetails/repoDetailsForm.tsx` — references query key

## Audit Logging

Preserve existing audit log entries and add new ones:

| Endpoint                                            | Audit Event                        |
| --------------------------------------------------- | ---------------------------------- |
| `PUT /seer/projects/`                               | `AUTOFIX_SETTINGS_EDIT` (existing) |
| `PUT /seer/projects/{project}/`                     | `AUTOFIX_SETTINGS_EDIT` (existing) |
| `POST/PUT/DELETE .../repos/`                        | New: `SEER_REPO_CONFIG_EDIT`       |
| `PUT /seer/repos/` and `PUT /seer/repos/{repo_id}/` | `REPO_SETTINGS_EDIT` (existing)    |
