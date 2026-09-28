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
from .github_write_service import GitHubWriteService
from .models import AuditLog, GitHubConnection, Project
from .setup_service import RepositorySetupService


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

    def post(self, request):
        """
        Public POST entrypoint with a last-resort JSON error boundary.

        This prevents Django's default HTML 500 page from hiding an
        unexpected exception occurring before/after the setup pipeline.
        """
        try:
            return self._post_impl(request)
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

    def _post_impl(self, request):
        project_id = request.data.get(
            "project_id"
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
                    "ci_secret_name": provisioning.secret_name,
                    "ci_variable_name": provisioning.variable_name,
                    "ci_token_id": provisioning.token_id,
                    "ci_tokens_rotated": (
                        provisioning.rotated_existing_tokens
                    ),
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
