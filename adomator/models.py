"""Pydantic data models for the adomator YAML configuration schema."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Branch policy models
# ---------------------------------------------------------------------------


class ReviewerPolicy(BaseModel):
    """Require a minimum number of reviewers before a PR can be completed."""

    enabled: bool = True
    blocking: bool = True
    minimum_approver_count: int = Field(default=1, ge=0)
    creator_vote_counts: bool = False
    allow_downvotes: bool = False
    reset_on_source_push: bool = False
    reset_on_push_to_pr_source_branch: bool = False
    require_vote_on_last_iteration: bool = False


class CommentPolicy(BaseModel):
    """Require all comments to be resolved before completion."""

    enabled: bool = True
    blocking: bool = True


class MergeStrategyPolicy(BaseModel):
    """Restrict which merge strategies are allowed."""

    enabled: bool = True
    blocking: bool = True
    allow_squash: bool = True
    allow_no_fast_forward: bool = False
    allow_rebase: bool = False
    allow_rebase_merge: bool = False


class WorkItemPolicy(BaseModel):
    """Require linked work items."""

    enabled: bool = True
    blocking: bool = False


class BuildValidationPolicy(BaseModel):
    """Trigger a build and require it to succeed."""

    display_name: str
    build_definition_id: int
    queue_on_source_update: bool = True
    valid_duration: float = Field(default=720.0, ge=0)
    enabled: bool = True
    blocking: bool = True


class StatusPolicy(BaseModel):
    """Require an external status check (generic status)."""

    status_name: str
    status_genre: str = "default"
    authorized_user: str | None = None
    invalidate_on_source_update: bool = True
    display_name: str | None = None
    enabled: bool = True
    blocking: bool = True


class BranchPolicies(BaseModel):
    """All policies that can be applied to a single branch."""

    reviewer: ReviewerPolicy | None = None
    comment: CommentPolicy | None = None
    merge_strategy: MergeStrategyPolicy | None = None
    work_item: WorkItemPolicy | None = None
    build_validations: list[BuildValidationPolicy] = Field(default_factory=list)
    statuses: list[StatusPolicy] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Repository-level settings
# ---------------------------------------------------------------------------


class RepositorySettings(BaseModel):
    """Core repository properties."""

    default_branch: str = "refs/heads/main"
    is_disabled: bool = False

    @field_validator("default_branch")
    @classmethod
    def normalise_branch(cls, v: str) -> str:
        if not v.startswith("refs/heads/"):
            return f"refs/heads/{v}"
        return v


# ---------------------------------------------------------------------------
# Security / ACL models
# ---------------------------------------------------------------------------


class PermissionEntry(BaseModel):
    """Permission assignment for a single group or user.

    ``allow`` and ``deny`` are lists of Azure DevOps Git permission names:
    ``GenericRead``, ``GenericContribute``, ``ForcePush``,
    ``CreateBranch``, ``CreateTag``, ``ManageNote``,
    ``PolicyExempt``, ``CreateRepository``, ``DeleteRepository``,
    ``RenameRepository``, ``EditPolicies``, ``RemoveOthersLocks``,
    ``ManagePermissions``, ``PullRequestContribute``,
    ``PullRequestBypassPolicy``.
    """

    principal: str = Field(
        ..., description="Group/user display name or descriptor (e.g. '[Project]\\\\Contributors')"
    )
    allow: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class SecuritySettings(BaseModel):
    """Security settings for a repository."""

    permissions: list[PermissionEntry] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-repository override block
# ---------------------------------------------------------------------------


class RepositoryOverride(BaseModel):
    """Per-repository overrides; absent fields fall back to defaults."""

    name: str = Field(..., description="Repository name inside the project")
    repository: RepositorySettings | None = None
    branch_policies: dict[str, BranchPolicies] = Field(
        default_factory=dict,
        description="Mapping of branch name (e.g. 'main') to its policies",
    )
    security: SecuritySettings | None = None


# ---------------------------------------------------------------------------
# Top-level project configuration
# ---------------------------------------------------------------------------


class DefaultSettings(BaseModel):
    """Project-wide defaults applied to every repository unless overridden."""

    repository: RepositorySettings = Field(default_factory=RepositorySettings)
    branch_policies: dict[str, BranchPolicies] = Field(default_factory=dict)
    security: SecuritySettings = Field(default_factory=SecuritySettings)


class ProjectConfig(BaseModel):
    """Root configuration document – one file per Azure DevOps project."""

    organization: str = Field(..., description="Azure DevOps organization name or URL")
    project: str = Field(..., description="Azure DevOps project name")
    token: str = Field(
        ...,
        description="Personal Access Token (PAT). Use '$ENV_VAR' syntax to read from environment",
    )
    defaults: DefaultSettings = Field(default_factory=DefaultSettings)
    repositories: list[RepositoryOverride] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    def effective_settings(self, repo_override: RepositoryOverride) -> dict[str, Any]:
        """Merge default settings with per-repo overrides and return effective config."""
        repo_settings = (
            repo_override.repository
            if repo_override.repository is not None
            else self.defaults.repository
        )

        # Deep-merge branch policies: start with defaults, override with repo-specific
        merged_policies: dict[str, BranchPolicies] = dict(self.defaults.branch_policies)
        for branch, policies in repo_override.branch_policies.items():
            if branch in merged_policies:
                # Merge field by field
                base = merged_policies[branch].model_dump()
                override = policies.model_dump(exclude_none=True)
                base.update(override)
                merged_policies[branch] = BranchPolicies(**base)
            else:
                merged_policies[branch] = policies

        # Security: repo overrides wins completely; otherwise use defaults
        security = (
            repo_override.security
            if repo_override.security is not None
            else self.defaults.security
        )

        return {
            "repository": repo_settings,
            "branch_policies": merged_policies,
            "security": security,
        }
