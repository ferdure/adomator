"""Azure DevOps REST API client wrapper for adomator.

Provides thin, tested wrappers around the azure-devops Python SDK to retrieve
and update repository settings, branch policies, and security ACLs.
"""

from __future__ import annotations

import hashlib
from typing import Any

from azure.devops.connection import Connection
from azure.devops.v7_1.git.models import GitRepository
from azure.devops.v7_1.policy.models import PolicyConfiguration, PolicyTypeRef
from azure.devops.v7_1.security.models import AccessControlEntry
from msrest.authentication import BasicAuthentication


# Known Azure DevOps branch policy type GUIDs
# (these are stable across all organisations)
POLICY_TYPE_MIN_REVIEWERS = "fa4e907d-c16b-452d-8106-7efa0cb84489"
POLICY_TYPE_COMMENT_REQUIREMENTS = "c6a1889d-b943-4856-b76f-9e46bb6b0df3"
POLICY_TYPE_MERGE_STRATEGY = "fa4e907d-c16b-452d-8106-7efa0cb84487"
POLICY_TYPE_BUILD_VALIDATION = "0609b952-1397-4640-95ec-e00a01b2cbcb"
POLICY_TYPE_WORK_ITEM_LINKING = "40e92b44-2fe1-4dd6-b3d8-74a9c21d0c6e"
POLICY_TYPE_STATUS_CHECK = "cbdc66da-9728-4af8-aada-9a5a32e4a226"

# Git security namespace ID (stable)
GIT_SECURITY_NAMESPACE_ID = "2e9eb7ed-3c0a-47d4-87c1-0ffdd275fd87"

# Git repository permission bit flags
GIT_PERMISSION_BITS: dict[str, int] = {
    "GenericRead": 2,
    "GenericContribute": 4,
    "ForcePush": 8,
    "CreateBranch": 16,
    "CreateTag": 32,
    "ManageNote": 64,
    "PolicyExempt": 128,
    "CreateRepository": 256,
    "DeleteRepository": 512,
    "RenameRepository": 1024,
    "EditPolicies": 2048,
    "RemoveOthersLocks": 4096,
    "ManagePermissions": 8192,
    "PullRequestContribute": 16384,
    "PullRequestBypassPolicy": 32768,
}


def _make_org_url(organization: str) -> str:
    """Return a fully qualified Azure DevOps organisation URL."""
    if organization.startswith("https://"):
        return organization.rstrip("/")
    return f"https://dev.azure.com/{organization}"


def _repo_security_token(project_id: str, repo_id: str) -> str:
    """Return the ACL token for a git repository."""
    return f"repoV2/{project_id}/{repo_id}"


def _bits_for_names(names: list[str]) -> int:
    """Convert a list of permission names into a combined bit mask."""
    bits = 0
    for name in names:
        bit = GIT_PERMISSION_BITS.get(name)
        if bit is None:
            raise ValueError(f"Unknown Git permission name: '{name}'")
        bits |= bit
    return bits


class AzureDevOpsClient:
    """Thin wrapper around the azure-devops SDK for adomator operations."""

    def __init__(self, organization: str, token: str) -> None:
        credentials = BasicAuthentication("", token)
        org_url = _make_org_url(organization)
        self._connection = Connection(base_url=org_url, creds=credentials)
        self._git = self._connection.clients.get_git_client()
        self._policy = self._connection.clients.get_policy_client()
        self._security = self._connection.clients.get_security_client()
        self._core = self._connection.clients.get_core_client()
        self._graph = self._connection.clients.get_graph_client()

    # ------------------------------------------------------------------
    # Repository operations
    # ------------------------------------------------------------------

    def list_repositories(self, project: str) -> list[GitRepository]:
        """Return all repositories in *project*."""
        return self._git.get_repositories(project=project) or []

    def get_repository(self, project: str, repo_name: str) -> GitRepository | None:
        """Return a repository by name, or ``None`` if not found."""
        repos = self.list_repositories(project)
        for repo in repos:
            if repo.name == repo_name:
                return repo
        return None

    def update_repository(
        self,
        project: str,
        repo_id: str,
        default_branch: str | None = None,
        is_disabled: bool | None = None,
    ) -> GitRepository:
        """Update mutable repository properties."""
        update: dict[str, Any] = {}
        if default_branch is not None:
            update["defaultBranch"] = default_branch
        if is_disabled is not None:
            update["isDisabled"] = is_disabled
        return self._git.update_repository(
            new_repository_info=update,
            repository_id=repo_id,
            project=project,
        )

    # ------------------------------------------------------------------
    # Policy operations
    # ------------------------------------------------------------------

    def get_policy_configurations(self, project: str) -> list[PolicyConfiguration]:
        """Return all policy configurations in *project*."""
        return self._policy.get_policy_configurations(project=project) or []

    def get_repo_policy_configurations(
        self, project: str, repo_id: str
    ) -> list[PolicyConfiguration]:
        """Return all policy configurations scoped to a specific repository."""
        all_policies = self.get_policy_configurations(project)
        result = []
        for p in all_policies:
            scope = (p.settings or {}).get("scope", [])
            for s in scope:
                if s.get("repositoryId") == repo_id:
                    result.append(p)
                    break
        return result

    def create_policy(
        self, project: str, policy_type_id: str, settings: dict[str, Any], blocking: bool, enabled: bool
    ) -> PolicyConfiguration:
        """Create a new policy configuration."""
        config = PolicyConfiguration(
            is_blocking=blocking,
            is_enabled=enabled,
            type=PolicyTypeRef(id=policy_type_id),
            settings=settings,
        )
        return self._policy.create_policy_configuration(
            configuration=config, project=project
        )

    def update_policy(
        self,
        project: str,
        policy_id: int,
        policy_type_id: str,
        settings: dict[str, Any],
        blocking: bool,
        enabled: bool,
    ) -> PolicyConfiguration:
        """Update an existing policy configuration."""
        config = PolicyConfiguration(
            id=policy_id,
            is_blocking=blocking,
            is_enabled=enabled,
            type=PolicyTypeRef(id=policy_type_id),
            settings=settings,
        )
        return self._policy.update_policy_configuration(
            configuration=config,
            project=project,
            configuration_id=policy_id,
        )

    def delete_policy(self, project: str, policy_id: int) -> None:
        """Delete a policy configuration."""
        self._policy.delete_policy_configuration(project=project, configuration_id=policy_id)

    # ------------------------------------------------------------------
    # Security / ACL operations
    # ------------------------------------------------------------------

    def get_project_descriptor(self, project_name: str) -> str:
        """Return the security descriptor for a project by name."""
        projects = self._core.get_projects() or []
        for p in projects:
            if p.name == project_name:
                return self._graph.get_descriptor(p.id).value
        raise ValueError(f"Project not found: '{project_name}'")

    def resolve_principal_descriptor(
        self, project: str, principal_name: str
    ) -> str | None:
        """Resolve a group display name or descriptor to a security identity descriptor.

        Returns the SID (security identity descriptor) string used in ACLs, or
        ``None`` if the principal cannot be resolved.
        """
        try:
            project_descriptor = self.get_project_descriptor(project)
            groups = self._graph.list_groups(scope_descriptor=project_descriptor).graph_members or []
            for group in groups:
                if (
                    group.display_name == principal_name
                    or group.principal_name == principal_name
                    or group.descriptor == principal_name
                ):
                    # Resolve to storage key (SID)
                    subject_lookup = self._graph.lookup_subjects(
                        subject_lookup={"lookupKeys": [{"descriptor": group.descriptor}]}
                    )
                    for _, subject in (subject_lookup or {}).items():
                        return subject.descriptor
                    return group.descriptor
        except Exception:
            pass
        return None

    def set_repo_permissions(
        self,
        project_id: str,
        repo_id: str,
        descriptor: str,
        allow_bits: int,
        deny_bits: int,
    ) -> None:
        """Set ACL entries for *descriptor* on a repository."""
        token = _repo_security_token(project_id, repo_id)
        container = {
            "token": token,
            "merge": True,
            "accessControlEntries": [
                {
                    "descriptor": descriptor,
                    "allow": allow_bits,
                    "deny": deny_bits,
                }
            ],
        }
        self._security.set_access_control_entries(
            security_namespace_id=GIT_SECURITY_NAMESPACE_ID,
            container=container,
        )

    def query_repo_acl(self, project_id: str, repo_id: str) -> list[dict[str, Any]]:
        """Return ACL entries for a repository."""
        token = _repo_security_token(project_id, repo_id)
        acls = self._security.query_access_control_lists(
            security_namespace_id=GIT_SECURITY_NAMESPACE_ID,
            token=token,
            include_extended_info=True,
        ) or []
        result = []
        for acl in acls:
            for descriptor, entry in (acl.aces_dictionary or {}).items():
                result.append(
                    {
                        "descriptor": descriptor,
                        "allow": entry.allow,
                        "deny": entry.deny,
                    }
                )
        return result
