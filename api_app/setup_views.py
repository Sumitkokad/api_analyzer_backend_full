"""
API endpoint for automatic GitHub repository onboarding.

Flow:

Authenticated user
    -> project
    -> connected GitHub repository
    -> repository scan
    -> adapter detection
    -> setup plan
    -> GitHub setup branch
    -> setup files
    -> setup pull request

The endpoint never asks the user for a GitHub PAT or CI token.
"""

from __future__ import annotations
from django.http import JsonResponse
from typing import Any
from urllib.parse import quote

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .adapters.registry import AdapterRegistry
from .github_actions_provisioning_service import (
    GitHubActionsProvisioningService,
)
from .github_scan_service import GitHubRepositoryScanner
from .github_service import (
    GitHubAPIError,
    GitHubAppClient,
    GitHubConfigurationError,
)
from .github_write_service import (
    GitHubWriteService,
    SetupExecutionResult,
)
from .models import AuditLog, GitHubConnection, Project
from .setup_service import RepositorySetupService


SETUP_AUDIT_ACTION = "github_setup_pull_request_created"
DEFAULT_SETUP_FILES = (
    ".api-analyzer.yml",
    ".github/workflows/api-compatibility.yml",
)


def _project_for_user(
    request: Any,
    project_id: Any,
) -> Project | None:
    try:
        return Project.objects.get(
            pk=project_id,
            owner=request.user,
        )
    except Project.DoesNotExist:
        return None


class GitHubRepositorySetupView(APIView):
    """
    Create the API Analyzer setup pull request for a connected repository.

    The request only needs the project ID. Repository, installation,
    framework, and contract information are obtained from the existing
    GitHub App connection and repository scan.
    """

    permission_classes = (IsAuthenticated,)

    def get(self, request, project_id=None):
        """
        Return the persisted one-time setup state without changing GitHub.

        This lets the frontend restore the correct disabled state after a
        page refresh while keeping POST responsible for setup creation or
        recovery.
        """
        try:
            selected_project_id = (
                project_id
                or request.query_params.get("project_id")
            )

            if not selected_project_id:
                return Response(
                    {
                        "detail": "project_id is required."
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            project = _project_for_user(
                request,
                selected_project_id,
            )

            if project is None:
                return Response(
                    {
                        "detail": "Project not found."
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            try:
                connection = GitHubConnection.objects.get(
                    project=project
                )
            except GitHubConnection.DoesNotExist:
                return Response(
                    {
                        "success": True,
                        "already_exists": False,
                        "status": "not_configured",
                        "repository": "",
                        "setup": None,
                        "adapter": None,
                    },
                    status=status.HTTP_200_OK,
                )

            repository_full_name = str(
                connection.repository_full_name
                or project.repository_full_name
                or ""
            ).strip()

            existing_setup = self._latest_setup_audit(
                project=project,
                repository_full_name=repository_full_name,
            )

            if existing_setup is None:
                return Response(
                    {
                        "success": True,
                        "already_exists": False,
                        "status": "not_configured",
                        "repository": repository_full_name,
                        "setup": None,
                        "adapter": None,
                    },
                    status=status.HTTP_200_OK,
                )

            metadata = existing_setup.metadata or {}
            self._store_detected_configuration_from_audit(
                project=project,
                metadata=metadata,
            )

            return self._response_from_setup_metadata(
                repository_full_name=repository_full_name,
                metadata=metadata,
                already_exists=True,
                message=(
                    "API compatibility setup already exists for this "
                    "repository."
                ),
            )

        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception(
                "Unable to read automatic GitHub setup state."
            )

            return Response(
                {
                    "detail": "Unable to read API Analyzer setup state.",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def dispatch(self, request, *args, **kwargs):
        """
        Last-resort JSON boundary for the complete request lifecycle.
        """
        try:
            return super().dispatch(request, *args, **kwargs)

        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception(
                "UNHANDLED SETUP DISPATCH FAILURE"
            )

            return JsonResponse(
                {
                    "detail": "Automatic repository setup failed before the setup handler completed.",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                status=500,
            )

    def post(self, request, project_id=None):
        """
        Public POST entrypoint with a last-resort JSON error boundary.

        This prevents Django's default HTML 500 page from hiding an
        unexpected exception occurring before/after the setup pipeline.
        """
        try:
            return self._post_impl(
                request,
                project_id=project_id,
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception(
                "UNHANDLED SETUP VIEW FAILURE"
            )

            return Response(
                {
                    "detail": "Automatic repository setup failed.",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    def _post_impl(self, request, *, project_id=None):
        project_id = (
            project_id
            or request.data.get("project_id")
        )

        if not project_id:
            return Response(
                {
                    "detail": (
                        "project_id is required."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = _project_for_user(
            request,
            project_id,
        )

        if project is None:
            return Response(
                {
                    "detail": "Project not found."
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            connection = (
                GitHubConnection.objects
                .get(project=project)
            )
        except GitHubConnection.DoesNotExist:
            return Response(
                {
                    "detail": (
                        "No GitHub App connection exists "
                        "for this project."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if not connection.connected:
            return Response(
                {
                    "detail": (
                        "The GitHub repository connection "
                        "is not active."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        repository_full_name = str(
            connection.repository_full_name
            or project.repository_full_name
            or ""
        ).strip()

        if not repository_full_name:
            return Response(
                {
                    "detail": (
                        "No GitHub repository has been selected "
                        "for this project."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        # Idempotency guard: setup is a one-time repository operation.
        # A repeated browser click must return the already-created PR instead
        # of attempting to recreate the deterministic setup branch.
        existing_setup = self._latest_setup_audit(
            project=project,
            repository_full_name=repository_full_name,
        )

        if existing_setup is not None:
            self._store_detected_configuration_from_audit(
                project=project,
                metadata=existing_setup.metadata or {},
            )
            return self._response_from_setup_metadata(
                repository_full_name=repository_full_name,
                metadata=existing_setup.metadata or {},
                already_exists=True,
                message=(
                    "API compatibility setup already exists for this "
                    "repository."
                ),
            )

        try:
            installation_id = int(
                connection.installation_id
            )
        except (TypeError, ValueError):
            return Response(
                {
                    "detail": (
                        "GitHub installation ID is invalid."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        if installation_id <= 0:
            return Response(
                {
                    "detail": (
                        "GitHub installation ID is invalid."
                    )
                },
                status=status.HTTP_409_CONFLICT,
            )

        requested_spec_path = str(
            request.data.get("spec_path")
            or project.spec_path
            or ""
        ).strip()

        try:
            github_client = GitHubAppClient()

            token_data = (
                github_client.create_installation_token(
                    installation_id
                )
            )

            installation_token = str(
                token_data.get("token")
                or ""
            ).strip()

            if not installation_token:
                raise GitHubAPIError(
                    "GitHub installation token was not returned."
                )

            scanner = GitHubRepositoryScanner(
                github_client=github_client,
                installation_token=installation_token,
            )

            scan_result = scanner.scan(
                repository_full_name,
                default_branch=(
                    project.default_branch
                    or None
                ),
            )

            repository_metadata = (
                self._repository_metadata(
                    scan_result=scan_result,
                    repository_full_name=repository_full_name,
                    project=project,
                )
            )

            registry = (
                AdapterRegistry.with_defaults()
            )

            resolution = registry.detect(
                repository_metadata
            )

            if not resolution.supported:
                return Response(
                    {
                        "detail": (
                            "The repository was scanned successfully, "
                            "but no installed API Analyzer adapter "
                            "supports the detected technology."
                        ),
                        "repository": repository_full_name,
                        "scan": scan_result.as_dict(),
                        "adapter": None,
                        "reason": resolution.reason,
                        "errors": list(
                            resolution.errors
                        ),
                    },
                    status=status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            adapter = resolution.adapter

            spec_path = (
                requested_spec_path
                or (
                    scan_result.contract.path
                    if scan_result.contract.found
                    else ""
                )
                or None
            )

            setup_service = (
                RepositorySetupService()
            )

            setup_plan = setup_service.build_plan(
                repository=repository_metadata,
                adapter=adapter,
                base_branch=(
                    scan_result.default_branch
                    or project.default_branch
                    or "main"
                ),
                spec_path=spec_path,
            )

            # Second idempotency guard: the database audit log can be missing
            # when an earlier request successfully changed GitHub but failed
            # before the final database write. Query GitHub directly as the
            # source of truth before provisioning or writing anything again.
            existing_pr = self._find_existing_setup_pull_request(
                github_client=github_client,
                installation_token=installation_token,
                repository_full_name=repository_full_name,
                branch_name=setup_plan.branch_name,
            )

            if existing_pr is not None:
                metadata = self._metadata_from_pull_request(
                    pull_request=existing_pr,
                    setup_plan=setup_plan,
                )
                self._store_detected_configuration(
                    project=project,
                    scan_result=scan_result,
                    spec_path=setup_plan.spec_path,
                    adapter_type=setup_plan.adapter_type,
                )
                self._ensure_setup_audit(
                    project=project,
                    actor=request.user,
                    connection=connection,
                    metadata=metadata,
                )
                return self._response_from_setup_metadata(
                    repository_full_name=repository_full_name,
                    metadata=metadata,
                    already_exists=True,
                    scan=scan_result.as_dict(),
                    adapter={
                        "adapter_type": setup_plan.adapter_type,
                        "framework_name": setup_plan.framework_name,
                    },
                    message=(
                        "API compatibility setup pull request already "
                        "exists for this repository."
                    ),
                )

            existing_branch = self._github_branch_exists(
                github_client=github_client,
                installation_token=installation_token,
                repository_full_name=repository_full_name,
                branch_name=setup_plan.branch_name,
            )

            # Provision the project-scoped GitHub Actions credential before
            # creating the setup PR. The setup workflow created above reads
            # the project ID from the repository variable and the credential
            # from the repository Actions secret. No plaintext token is
            # returned to the browser or included in audit metadata.
            provisioning_service = (
                GitHubActionsProvisioningService(
                    github_client=github_client
                )
            )

            provisioning = provisioning_service.provision(
                project=project,
                installation_id=installation_id,
                repository_full_name=repository_full_name,
            )

            if existing_branch:
                # A previous attempt may have created the deterministic setup
                # branch but failed while writing files or creating the PR.
                # Reuse that branch, finish the files, and create exactly one
                # setup PR instead of failing on branch creation.
                execution = self._recover_existing_branch(
                    github_client=github_client,
                    installation_token=installation_token,
                    plan=setup_plan,
                )
            else:
                write_service = (
                    GitHubWriteService(
                        github_client=github_client
                    )
                )

                execution = (
                    write_service.execute_setup(
                        installation_id=installation_id,
                        plan=setup_plan,
                    )
                )

        except GitHubConfigurationError:
            return Response(
                {
                    "detail": (
                        "GitHub App is not configured "
                        "on the server."
                    )
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        except GitHubAPIError as exc:
            return Response(
                {
                    "detail": (
                        "GitHub setup could not be completed."
                    ),
                    "error": str(exc),
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        except ValueError as exc:
            return Response(
                {
                    "detail": str(exc)
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            import logging

            logging.getLogger(__name__).exception(
                "Automatic GitHub repository setup failed."
            )

            return Response(
                {
                    "detail": (
                        "Automatic repository setup failed "
                        "unexpectedly."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if not execution.success:
            # Handle concurrent requests and partial GitHub writes. One
            # request can win the branch/PR creation race while another sees
            # a 4xx from GitHub. Re-read GitHub before returning an error.
            try:
                recovered_pr = self._find_existing_setup_pull_request(
                    github_client=github_client,
                    installation_token=installation_token,
                    repository_full_name=repository_full_name,
                    branch_name=setup_plan.branch_name,
                )

                if recovered_pr is not None:
                    recovered_metadata = self._metadata_from_pull_request(
                        pull_request=recovered_pr,
                        setup_plan=setup_plan,
                    )
                    execution = self._execution_from_metadata(
                        repository_full_name=repository_full_name,
                        setup_plan=setup_plan,
                        metadata=recovered_metadata,
                    )
                    self._store_detected_configuration(
                        project=project,
                        scan_result=scan_result,
                        spec_path=setup_plan.spec_path,
                        adapter_type=setup_plan.adapter_type,
                    )
                    self._ensure_setup_audit(
                        project=project,
                        actor=request.user,
                        connection=connection,
                        metadata=recovered_metadata,
                    )
                else:
                    branch_now_exists = self._github_branch_exists(
                        github_client=github_client,
                        installation_token=installation_token,
                        repository_full_name=repository_full_name,
                        branch_name=setup_plan.branch_name,
                    )

                    if branch_now_exists:
                        execution = self._recover_existing_branch(
                            github_client=github_client,
                            installation_token=installation_token,
                            plan=setup_plan,
                        )
            except Exception:
                import logging

                logging.getLogger(__name__).exception(
                    "SETUP RECOVERY CHECK FAILED"
                )

        if not execution.success:
            return Response(
                {
                    "detail": (
                        "API Analyzer could not create "
                        "the setup pull request."
                    ),
                    "repository": repository_full_name,
                    "branch_name": execution.branch_name,
                    "base_branch": execution.base_branch,
                    "files_written": list(
                        execution.files_written
                    ),
                    "error": execution.error,
                    "warnings": list(
                        execution.warnings
                    ),
                    "metadata": execution.metadata,
                },
                status=status.HTTP_502_BAD_GATEWAY,
            )

        try:
            self._store_detected_configuration(
                project=project,
                scan_result=scan_result,
                spec_path=setup_plan.spec_path,
                adapter_type=setup_plan.adapter_type,
            )

            AuditLog.objects.create(
                project=project,
                actor=request.user,
                action="github_setup_pull_request_created",
                resource_type="GitHubConnection",
                resource_id=str(
                    connection.pk
                ),
                metadata={
                    "repository": repository_full_name,
                    "branch_name": execution.branch_name,
                    "base_branch": execution.base_branch,
                    "pull_request_number": (
                        execution.pull_request_number
                    ),
                    "pull_request_url": (
                        execution.pull_request_url
                    ),
                    "adapter_type": setup_plan.adapter_type,
                    "framework_name": setup_plan.framework_name,
                    "spec_path": setup_plan.spec_path,
                    "generation_command": setup_plan.generation_command,
                    "ci_secret_name": provisioning.secret_name,
                    "ci_variable_name": provisioning.variable_name,
                    "ci_token_id": provisioning.token_id,
                    "ci_tokens_rotated": (
                        provisioning.rotated_existing_tokens
                    ),
                    "files_written": list(
                        execution.files_written
                    ),
                    "setup_status": "setup_pending",
                },
            )
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception(
                "POST-SETUP FAILURE: final database update failed."
            )

            return Response(
                {
                    "detail": (
                        "Setup PR was created, but the final database "
                        "update failed."
                    ),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(
            {
                "success": True,
                "already_exists": False,
                "status": "setup_pending",
                "repository": repository_full_name,
                "scan": scan_result.as_dict(),
                "adapter": {
                    "adapter_type": setup_plan.adapter_type,
                    "framework_name": (
                        setup_plan.framework_name
                    ),
                },
                "setup": {
                    "branch_name": execution.branch_name,
                    "base_branch": execution.base_branch,
                    "files_written": list(
                        execution.files_written
                    ),
                    "pull_request_number": (
                        execution.pull_request_number
                    ),
                    "pull_request_url": (
                        execution.pull_request_url
                    ),
                    "spec_path": setup_plan.spec_path,
                    "generation_command": (
                        setup_plan.generation_command
                    ),
                    "ci_credentials": {
                        "secret_name": provisioning.secret_name,
                        "variable_name": provisioning.variable_name,
                        "token_id": provisioning.token_id,
                        "rotated_existing_tokens": (
                            provisioning.rotated_existing_tokens
                        ),
                    },
                    "warnings": list(
                        execution.warnings
                    ),
                },
            },
            status=status.HTTP_201_CREATED,
        )

    @staticmethod
    def _latest_setup_audit(
        *,
        project: Project,
        repository_full_name: str,
    ) -> AuditLog | None:
        """
        Return the most recent successful setup audit for this exact project
        and repository. This is the cheapest idempotency check and avoids a
        second GitHub setup when the request is repeated from the UI.
        """
        return (
            AuditLog.objects
            .filter(
                project=project,
                action=SETUP_AUDIT_ACTION,
                metadata__repository=repository_full_name,
            )
            .order_by("-created_at")
            .first()
        )

    @staticmethod
    def _response_from_setup_metadata(
        *,
        repository_full_name: str,
        metadata: dict[str, Any],
        already_exists: bool,
        scan: dict[str, Any] | None = None,
        adapter: dict[str, Any] | None = None,
        message: str,
    ) -> Response:
        stored_adapter = adapter or {
            "adapter_type": str(
                metadata.get("adapter_type") or ""
            ),
            "framework_name": str(
                metadata.get("framework_name") or ""
            ),
        }

        files_written = metadata.get(
            "files_written"
        )

        if not isinstance(files_written, list):
            files_written = list(DEFAULT_SETUP_FILES)

        setup_status = str(
            metadata.get("setup_status")
            or "setup_pending"
        )

        return Response(
            {
                "success": True,
                "already_exists": already_exists,
                "status": setup_status,
                "repository": repository_full_name,
                "scan": scan,
                "adapter": stored_adapter,
                "setup": {
                    "branch_name": str(
                        metadata.get("branch_name") or ""
                    ),
                    "base_branch": str(
                        metadata.get("base_branch") or "main"
                    ),
                    "files_written": files_written,
                    "pull_request_number": metadata.get(
                        "pull_request_number"
                    ),
                    "pull_request_url": metadata.get(
                        "pull_request_url"
                    ),
                    "spec_path": str(
                        metadata.get("spec_path") or ""
                    ),
                    "generation_command": str(
                        metadata.get("generation_command") or ""
                    ),
                    "warnings": list(
                        metadata.get("warnings") or []
                    ),
                },
                "message": message,
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _github_parts(
        repository_full_name: str,
    ) -> tuple[str, str]:
        parts = str(
            repository_full_name or ""
        ).strip().split("/", 1)

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the owner/repository format."
            )

        return parts[0], parts[1]

    @classmethod
    def _find_existing_setup_pull_request(
        cls,
        *,
        github_client: GitHubAppClient,
        installation_token: str,
        repository_full_name: str,
        branch_name: str,
    ) -> dict[str, Any] | None:
        """
        Find any PR for the deterministic API Analyzer setup branch.

        State=all is intentional. A merged/closed setup PR still means the
        repository has already gone through onboarding and must not get a
        second setup PR from a repeat click.
        """
        owner, repo = cls._github_parts(
            repository_full_name
        )

        data = github_client._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls",
            authorization=f"Bearer {installation_token}",
            params={
                "state": "all",
                "head": f"{owner}:{branch_name}",
                "per_page": 100,
            },
        )

        if not isinstance(data, list):
            return None

        pull_requests = [
            item
            for item in data
            if isinstance(item, dict)
        ]

        if not pull_requests:
            return None

        # Prefer open PRs, otherwise use the most recently returned PR.
        for pull_request in pull_requests:
            if str(
                pull_request.get("state") or ""
            ).lower() == "open":
                return pull_request

        return pull_requests[0]

    @classmethod
    def _github_branch_exists(
        cls,
        *,
        github_client: GitHubAppClient,
        installation_token: str,
        repository_full_name: str,
        branch_name: str,
    ) -> bool:
        owner, repo = cls._github_parts(
            repository_full_name
        )

        path = (
            f"/repos/{owner}/{repo}/git/ref/heads/"
            f"{quote(str(branch_name).strip(), safe='/')}"
        )

        try:
            data = github_client._request(
                "GET",
                path,
                authorization=f"Bearer {installation_token}",
            )
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return False
            raise

        return isinstance(data, dict)

    @staticmethod
    def _metadata_from_pull_request(
        *,
        pull_request: dict[str, Any],
        setup_plan: Any,
    ) -> dict[str, Any]:
        raw_number = pull_request.get("number")

        try:
            pull_request_number = (
                int(raw_number)
                if raw_number is not None
                else None
            )
        except (TypeError, ValueError):
            pull_request_number = None

        pull_request_url = str(
            pull_request.get("html_url") or ""
        ).strip() or None

        actual_base = str(
            (pull_request.get("base") or {}).get("ref")
            if isinstance(pull_request.get("base"), dict)
            else ""
        ).strip()

        return {
            "repository": setup_plan.repository,
            "branch_name": setup_plan.branch_name,
            "base_branch": actual_base or setup_plan.base_branch,
            "pull_request_number": pull_request_number,
            "pull_request_url": pull_request_url,
            "adapter_type": setup_plan.adapter_type,
            "framework_name": setup_plan.framework_name,
            "spec_path": setup_plan.spec_path,
            "generation_command": setup_plan.generation_command,
            "files_written": [
                item.path
                for item in setup_plan.files
            ],
            "setup_status": "setup_pending",
            "recovered_from_github": True,
        }

    @staticmethod
    def _execution_from_metadata(
        *,
        repository_full_name: str,
        setup_plan: Any,
        metadata: dict[str, Any],
    ) -> SetupExecutionResult:
        return SetupExecutionResult(
            success=True,
            repository=repository_full_name,
            branch_name=setup_plan.branch_name,
            base_branch=str(
                metadata.get("base_branch")
                or setup_plan.base_branch
            ),
            files_written=tuple(
                metadata.get("files_written") or []
            ),
            pull_request_number=metadata.get(
                "pull_request_number"
            ),
            pull_request_url=metadata.get(
                "pull_request_url"
            ),
            warnings=tuple(
                setup_plan.warnings
            ),
            metadata=metadata,
        )

    @staticmethod
    def _recover_existing_branch(
        *,
        github_client: GitHubAppClient,
        installation_token: str,
        plan: Any,
    ) -> SetupExecutionResult:
        files_written: list[str] = []

        try:
            for setup_file in plan.files:
                github_client.create_or_update_repository_file(
                    installation_token,
                    plan.repository,
                    path=setup_file.path,
                    content=setup_file.content,
                    branch=plan.branch_name,
                    commit_message=(
                        "chore: configure API compatibility analysis"
                    ),
                )
                files_written.append(
                    setup_file.path
                )

            pull_request = github_client.create_pull_request(
                installation_token,
                plan.repository,
                title=plan.pull_request_title,
                head=plan.branch_name,
                base=plan.base_branch,
                body=plan.pull_request_body,
            )

            raw_number = pull_request.get("number")
            try:
                pull_request_number = (
                    int(raw_number)
                    if raw_number is not None
                    else None
                )
            except (TypeError, ValueError):
                pull_request_number = None

            pull_request_url = str(
                pull_request.get("html_url") or ""
            ).strip() or None

            return SetupExecutionResult(
                success=True,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                pull_request_number=pull_request_number,
                pull_request_url=pull_request_url,
                warnings=tuple(plan.warnings),
                metadata={
                    "setup_mode": "automatic",
                    "review_required": True,
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                    "spec_path": plan.spec_path,
                    "recovered_existing_branch": True,
                },
            )

        except GitHubAPIError as exc:
            return SetupExecutionResult(
                success=False,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                warnings=tuple(plan.warnings),
                error=str(exc),
                metadata={
                    "setup_mode": "automatic",
                    "recovered_existing_branch": True,
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                    "failed_stage": (
                        "file_write"
                        if files_written
                        else "file_write"
                    ),
                },
            )

        except Exception as exc:
            return SetupExecutionResult(
                success=False,
                repository=plan.repository,
                branch_name=plan.branch_name,
                base_branch=plan.base_branch,
                files_written=tuple(files_written),
                warnings=tuple(plan.warnings),
                error=(
                    "Unexpected setup recovery failure: "
                    f"{exc}"
                ),
                metadata={
                    "setup_mode": "automatic",
                    "recovered_existing_branch": True,
                    "adapter_type": plan.adapter_type,
                    "framework_name": plan.framework_name,
                },
            )

    @staticmethod
    def _ensure_setup_audit(
        *,
        project: Project,
        actor: Any,
        connection: GitHubConnection,
        metadata: dict[str, Any],
    ) -> AuditLog:
        existing = GitHubRepositorySetupView._latest_setup_audit(
            project=project,
            repository_full_name=str(
                metadata.get("repository") or ""
            ),
        )

        if existing is not None:
            return existing

        return AuditLog.objects.create(
            project=project,
            actor=actor,
            action=SETUP_AUDIT_ACTION,
            resource_type="GitHubConnection",
            resource_id=str(connection.pk),
            metadata=metadata,
        )

    @staticmethod
    def _store_detected_configuration_from_audit(
        *,
        project: Project,
        metadata: dict[str, Any],
    ) -> None:
        update_fields: list[str] = []

        adapter_type = str(
            metadata.get("adapter_type") or ""
        ).strip()
        spec_path = str(
            metadata.get("spec_path") or ""
        ).strip()
        base_branch = str(
            metadata.get("base_branch") or ""
        ).strip()

        if adapter_type and project.adapter_type != adapter_type:
            project.adapter_type = adapter_type
            update_fields.append("adapter_type")

        if spec_path and project.spec_path != spec_path:
            project.spec_path = spec_path
            update_fields.append("spec_path")

        if base_branch and project.default_branch != base_branch:
            project.default_branch = base_branch
            update_fields.append("default_branch")

        if update_fields:
            update_fields.append("updated_at")
            project.save(update_fields=update_fields)

    @staticmethod
    def _repository_metadata(
        *,
        scan_result: Any,
        repository_full_name: str,
        project: Project,
    ) -> dict[str, Any]:
        """
        Convert the scanner result into the adapter registry's
        framework-agnostic repository metadata format.
        """

        tree_paths = tuple(
            getattr(
                scan_result,
                "tree_paths",
                (),
            )
            or ()
        )

        manifest_contents = dict(
            getattr(
                scan_result,
                "manifest_contents",
                {},
            )
            or {}
        )

        metadata: dict[str, Any] = {
            "repository_full_name": (
                repository_full_name
            ),
            "default_branch": (
                scan_result.default_branch
                or project.default_branch
                or "main"
            ),
            # Internal scanner evidence is passed to the adapter registry.
            # Manifest contents are never returned by scan_result.as_dict().
            "tree_paths": list(tree_paths),
            "manifest_contents": manifest_contents,
            "source_files": list(tree_paths),
            "warnings": list(
                scan_result.warnings
            ),
        }

        # The scanner's public result intentionally contains only the
        # onboarding-level information. The adapter registry can use the
        # stored project configuration as an additional signal.
        if scan_result.contract.found:
            metadata["spec_path"] = (
                scan_result.contract.path
            )

        if scan_result.framework.detected:
            metadata["detected_adapter_type"] = (
                scan_result.framework.adapter_type
            )

        return metadata

    @staticmethod
    def _store_detected_configuration(
        *,
        project: Project,
        scan_result: Any,
        spec_path: str,
        adapter_type: str,
    ) -> None:
        update_fields: list[str] = []

        if spec_path and project.spec_path != spec_path:
            project.spec_path = spec_path
            update_fields.append(
                "spec_path"
            )

        if (
            adapter_type
            and project.adapter_type != adapter_type
        ):
            project.adapter_type = adapter_type
            update_fields.append(
                "adapter_type"
            )

        if (
            scan_result.default_branch
            and project.default_branch
            != scan_result.default_branch
        ):
            project.default_branch = (
                scan_result.default_branch
            )
            update_fields.append(
                "default_branch"
            )

        if update_fields:
            update_fields.append(
                "updated_at"
            )

            project.save(
                update_fields=update_fields
            )


__all__ = [
    "GitHubRepositorySetupView",
]
