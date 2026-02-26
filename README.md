# adomator

**adomator** is a declarative, Terraform-style management tool for **Azure DevOps repositories**.

Describe the desired state of all repositories in a single YAML file per project, and let adomator apply the changes for you — repository settings, branch policies, and security permissions.

---

## Features

- **Declarative YAML configuration** – one file per Azure DevOps project.
- **Default + override model** – define project-wide defaults and override any setting per repository.
- **Full coverage of repo settings**: default branch, disabled state.
- **Branch policies**: minimum reviewers, comment requirements, merge strategy, work item linking, build validations, status checks.
- **Security/ACL management**: set allow/deny permission bits per group or user on each repository.
- **Desired-state reconciliation** – only changes that differ from the current live state are applied.
- **`plan` / `apply` workflow** – preview changes before applying them, just like Terraform.
- **Environment variable interpolation** – use `$VAR` or `${VAR}` placeholders in the YAML to keep secrets out of version control.

---

## Installation

```bash
pip install adomator
```

Or from source:

```bash
git clone https://github.com/ferdure/adomator.git
cd adomator
pip install -e .
```

---

## Quick start

### 1. Create a configuration file

```yaml
# my-project.yaml
organization: "my-org"
project: "my-project"
token: "${AZURE_DEVOPS_TOKEN}"

defaults:
  repository:
    default_branch: "main"
    is_disabled: false
  branch_policies:
    main:
      reviewer:
        minimum_approver_count: 1
        blocking: true
      merge_strategy:
        allow_squash: true
        blocking: true
  security:
    permissions:
      - principal: "[my-project]\\Contributors"
        allow: [GenericRead, GenericContribute, CreateBranch, PullRequestContribute]

repositories:
  - name: "service-alpha"           # uses all defaults

  - name: "service-beta"
    repository:
      default_branch: "develop"
    branch_policies:
      main:
        reviewer:
          minimum_approver_count: 2  # stricter than default
```

See [`examples/my-project.yaml`](examples/my-project.yaml) for a full example with all supported settings.

### 2. Export your PAT

```bash
export AZURE_DEVOPS_TOKEN="your-personal-access-token"
```

### 3. Preview changes (dry run)

```bash
adomator plan my-project.yaml
```

Output:
```
Plan: 2 change(s) to apply

  [update_repository] repository/service-beta
  [create_policy] repository/service-beta/branch/main/reviewer
```

### 4. Apply changes

```bash
adomator apply my-project.yaml
```

Use `--auto-approve` to skip the interactive confirmation prompt (e.g. in CI pipelines):

```bash
adomator apply --auto-approve my-project.yaml
```

---

## Configuration reference

### Top-level keys

| Key | Type | Required | Description |
|-----|------|----------|-------------|
| `organization` | string | ✅ | Azure DevOps organisation name or full URL |
| `project` | string | ✅ | Azure DevOps project name |
| `token` | string | ✅ | Personal Access Token (PAT). Supports `$ENV_VAR` substitution |
| `defaults` | object | | Project-wide defaults applied to all repositories |
| `repositories` | list | | List of repository declarations with optional overrides |

### `defaults` / per-repo block

```yaml
defaults:          # (or per-repo overrides block)
  repository:
    default_branch: "main"     # branch name (refs/heads/ prefix added automatically)
    is_disabled: false

  branch_policies:
    <branch-name>:             # e.g. "main", "develop"
      reviewer:
        enabled: true
        blocking: true
        minimum_approver_count: 1
        creator_vote_counts: false
        allow_downvotes: false
        reset_on_source_push: false
      comment:
        enabled: true
        blocking: true
      merge_strategy:
        enabled: true
        blocking: true
        allow_squash: true
        allow_no_fast_forward: false
        allow_rebase: false
        allow_rebase_merge: false
      work_item:
        enabled: true
        blocking: false
      build_validations:
        - display_name: "CI Build"
          build_definition_id: 42
          queue_on_source_update: true
          valid_duration: 720
          enabled: true
          blocking: true
      statuses:
        - status_name: "my-check"
          status_genre: "default"
          invalidate_on_source_update: true
          enabled: true
          blocking: true

  security:
    permissions:
      - principal: "[project]\\Group Name"
        allow:
          - GenericRead
          - GenericContribute
          - CreateBranch
          - PullRequestContribute
        deny: []
```

### Available Git permission names

`GenericRead`, `GenericContribute`, `ForcePush`, `CreateBranch`, `CreateTag`,
`ManageNote`, `PolicyExempt`, `CreateRepository`, `DeleteRepository`,
`RenameRepository`, `EditPolicies`, `RemoveOthersLocks`, `ManagePermissions`,
`PullRequestContribute`, `PullRequestBypassPolicy`.

---

## Development

```bash
# Install dev dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Run tests with verbose output
pytest -v
```

---

## License

MIT
