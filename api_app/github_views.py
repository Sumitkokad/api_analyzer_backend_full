from __future__ import annotations

import secrets
from datetime import timedelta
from urllib.parse import urlencode

from django.conf import settings
from django.db import transaction
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .github_service import (
    GitHubAPIError,
    GitHubAppClient,
    GitHubConfigurationError,
    hash_install_state,
)
from .models import (
    AuditLog,
    GitHubConnection,
    GitHubInstallState,
    Project,
)


def _frontend_result_url(success: bool, **params: object) -> str:
    base = getattr(
        settings,
        "GITHUB_APP_FRONTEND_URL",
        "http://localhost:5173",
    ).rstrip("/")
    query = {"github": "connected" if success else "error"}
    query.update({key: str(value) for key, value in params.items()})
    return f"{base}/?{urlencode(query)}"


def _project_for_user(request, project_id):
    try:
        return Project.objects.get(
            pk=project_id,
            owner=request.user,
        )
    except Project.DoesNotExist:
        return None


def _serialize_repository(repo: dict) -> dict:
    owner = repo.get("owner") or {}
    permissions = repo.get("permissions") or {}
    return {
        "id": repo.get("id"),
        "name": repo.get("name"),
        "full_name": repo.get("full_name"),
        "private": bool(repo.get("private")),
        "html_url": repo.get("html_url"),
        "default_branch": repo.get("default_branch") or "",
        "owner": owner.get("login") or "",
        "visibility": repo.get("visibility") or "",
        "permissions": permissions,
    }


class GitHubInstallStartView(APIView):
    """Create a one-time state and return the GitHub App install URL."""

    permission_classes = (IsAuthenticated,)

    def get(self, request):
        project_id = request.query_params.get("project_id")
        if not project_id:
            return Response(
                {"detail": "project_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        project = _project_for_user(request, project_id)
        if project is None:
            return Response(
                {"detail": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        try:
            client = GitHubAppClient()
            client.app_slug
            client.callback_url
            client.client_id
        except GitHubConfigurationError:
            return Response(
                {"detail": "GitHub App is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        now = timezone.now()
        GitHubInstallState.objects.filter(
            expires_at__lt=now,
            consumed_at__isnull=True,
        ).delete()

        raw_state = secrets.token_urlsafe(32)
        ttl = max(
            60,
            int(
                getattr(
                    settings,
                    "GITHUB_INSTALL_STATE_TTL_SECONDS",
                    600,
                )
            ),
        )

        GitHubInstallState.objects.create(
            project=project,
            state_hash=hash_install_state(raw_state),
            expires_at=now + timedelta(seconds=ttl),
        )

        install_url = (
            f"https://github.com/apps/{client.app_slug}/installations/new?"
            f"{urlencode({'state': raw_state})}"
        )

        return Response(
            {
                "install_url": install_url,
                "project_id": project.id,
                "expires_in": ttl,
            },
            status=status.HTTP_200_OK,
        )

class GitHubInstallCallbackView(APIView):
    """Handle GitHub App OAuth installation callback."""

    permission_classes = (AllowAny,)
    authentication_classes = ()

    def get(self, request):
        state = str(
            request.query_params.get("state") or ""
        ).strip()

        code = str(
            request.query_params.get("code") or ""
        ).strip()

        if not state:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    reason="missing_state",
                )
            )

        state_record = (
            GitHubInstallState.objects
            .select_related("project")
            .filter(
                state_hash=hash_install_state(state),
                consumed_at__isnull=True,
                expires_at__gt=timezone.now(),
            )
            .first()
        )

        if state_record is None:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    reason="invalid_or_expired_state",
                )
            )

        if not code:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    project_id=state_record.project_id,
                    reason="github_authorization_incomplete",
                )
            )

        try:
            # Consume the state exactly once.
            with transaction.atomic():
                locked_state = (
                    GitHubInstallState.objects
                    .select_for_update()
                    .get(pk=state_record.pk)
                )

                if (
                    locked_state.consumed_at is not None
                    or locked_state.expires_at <= timezone.now()
                ):
                    return HttpResponseRedirect(
                        _frontend_result_url(
                            False,
                            project_id=state_record.project_id,
                            reason="invalid_or_expired_state",
                        )
                    )

                locked_state.consumed_at = timezone.now()
                locked_state.save(
                    update_fields=[
                        "consumed_at",
                        "updated_at",
                    ]
                )

            client = GitHubAppClient()

            # --------------------------------------------------------------
            # 1. Exchange OAuth code for GitHub user access token.
            # --------------------------------------------------------------
            oauth = client.exchange_user_code(code)

            user_access_token = str(
                oauth["access_token"]
            )

            # --------------------------------------------------------------
            # 2. Get the GitHub user who authorized the App.
            # --------------------------------------------------------------
            github_user = client.get_authenticated_user(
                user_access_token
            )

            github_login = str(
                github_user.get("login") or ""
            ).strip()

            if not github_login:
                raise GitHubAPIError(
                    "GitHub authorization did not return a username."
                )

            # --------------------------------------------------------------
            # 3. Find this App's installation for the GitHub user.
            #
            # This uses:
            # GET /users/{username}/installation
            #
            # It avoids relying on the installation_id being present
            # in the OAuth callback query parameters.
            # --------------------------------------------------------------
            installation = client.get_user_installation(
                github_login
            )

            if not installation:
                raise GitHubAPIError(
                    "No API Analyzer installation was found "
                    "for the authorized GitHub user."
                )

            # --------------------------------------------------------------
            # 4. Read and validate installation ID.
            # --------------------------------------------------------------
            try:
                installation_id = int(
                    installation.get("id")
                )
            except (TypeError, ValueError) as exc:
                raise GitHubAPIError(
                    "GitHub returned an invalid installation ID."
                ) from exc

            if installation_id <= 0:
                raise GitHubAPIError(
                    "GitHub returned an invalid installation ID."
                )

            # --------------------------------------------------------------
            # 5. Verify that the installation belongs to this App.
            # --------------------------------------------------------------
            try:
                installation_app_id = int(
                    installation.get("app_id")
                )
            except (TypeError, ValueError) as exc:
                raise GitHubAPIError(
                    "GitHub installation does not contain "
                    "a valid App ID."
                ) from exc

            if installation_app_id != client.app_id:
                raise GitHubAPIError(
                    "The installation does not belong "
                    "to this GitHub App."
                )

            # --------------------------------------------------------------
            # 6. Store installation metadata.
            # --------------------------------------------------------------
            account = (
                installation.get("account")
                or {}
            )

            metadata = {
                "account_login": (
                    account.get("login")
                    or ""
                ),
                "account_id": account.get("id"),
                "account_type": (
                    account.get("type")
                    or ""
                ),
                "target_type": (
                    installation.get("target_type")
                    or ""
                ),
                "repository_selection": (
                    installation.get(
                        "repository_selection"
                    )
                    or ""
                ),
                "permissions": (
                    installation.get("permissions")
                    or {}
                ),
                "events": (
                    installation.get("events")
                    or []
                ),
                "app_slug": (
                    installation.get("app_slug")
                    or client.app_slug
                ),
                "github_authorized_user_login": github_login,
                "github_authorized_user_id": (
                    github_user.get("id")
                ),
            }

            # --------------------------------------------------------------
            # 7. Save the GitHub App installation for this project.
            # --------------------------------------------------------------
            connection, _ = (
                GitHubConnection.objects.update_or_create(
                    project=state_record.project,
                    defaults={
                        "installation_id": str(
                            installation_id
                        ),
                        "repository_full_name": "",
                        "connected": False,
                        "metadata": metadata,
                    },
                )
            )

            # --------------------------------------------------------------
            # 8. Audit log.
            # --------------------------------------------------------------
            AuditLog.objects.create(
                project=state_record.project,
                actor=state_record.project.owner,
                action="github_installation_connected",
                resource_type="GitHubConnection",
                resource_id=str(connection.pk),
                metadata={
                    "installation_id": str(
                        installation_id
                    ),
                    "account_login": metadata.get(
                        "account_login",
                        "",
                    ),
                },
            )

            # Installation is connected.
            # Repository selection comes next.
            return HttpResponseRedirect(
                _frontend_result_url(
                    True,
                    project_id=state_record.project_id,
                )
            )

        except GitHubConfigurationError:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    project_id=state_record.project_id,
                    reason="github_app_not_configured",
                )
            )

        except GitHubAPIError:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    project_id=state_record.project_id,
                    reason="github_installation_verification_failed",
                )
            )

        except Exception:
            return HttpResponseRedirect(
                _frontend_result_url(
                    False,
                    project_id=state_record.project_id,
                    reason="github_connection_failed",
                )
            )

class GitHubConnectionView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        project_id = request.query_params.get("project_id")
        project = _project_for_user(request, project_id)
        if project is None:
            return Response(
                {"detail": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        connection = getattr(project, "github_connection", None)
        if connection is None:
            return Response(
                {
                    "connected": False,
                    "installation_connected": False,
                    "repository_full_name": "",
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            {
                "connected": bool(connection.connected),
                "installation_connected": True,
                "installation_id": connection.installation_id,
                "repository_full_name": connection.repository_full_name,
                "metadata": connection.metadata or {},
            },
            status=status.HTTP_200_OK,
        )


class GitHubRepositoryListView(APIView):
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        project_id = request.query_params.get("project_id")
        project = _project_for_user(request, project_id)
        if project is None:
            return Response(
                {"detail": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        connection = getattr(project, "github_connection", None)
        if connection is None or not connection.installation_id:
            return Response(
                {"detail": "Connect the GitHub App before listing repositories."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            installation_id = int(connection.installation_id)
            client = GitHubAppClient()
            token_data = client.create_installation_token(installation_id)
            result = client.list_installation_repositories(
                str(token_data["token"]),
                page=int(request.query_params.get("page", 1)),
                per_page=int(request.query_params.get("per_page", 100)),
            )
        except (ValueError, TypeError):
            return Response(
                {"detail": "Invalid repository pagination parameters."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except GitHubConfigurationError:
            return Response(
                {"detail": "GitHub App is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except GitHubAPIError:
            return Response(
                {"detail": "Unable to fetch repositories from GitHub."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        repositories = [
            _serialize_repository(repo)
            for repo in result.get("repositories", [])
        ]

        return Response(
            {
                "project_id": project.id,
                "connection": {
                    "installation_id": connection.installation_id,
                    "account_login": (connection.metadata or {}).get(
                        "account_login", ""
                    ),
                    "repository_selection": (connection.metadata or {}).get(
                        "repository_selection", ""
                    ),
                },
                "repositories": repositories,
                "total_count": result.get("total_count", len(repositories)),
                "page": int(request.query_params.get("page", 1)),
                "per_page": int(request.query_params.get("per_page", 100)),
            },
            status=status.HTTP_200_OK,
        )


class GitHubRepositoryConnectView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        project_id = request.data.get("project_id")
        repository_full_name = str(
            request.data.get("repository_full_name") or ""
        ).strip()

        project = _project_for_user(request, project_id)
        if project is None:
            return Response(
                {"detail": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if (
            not repository_full_name
            or repository_full_name.count("/") != 1
            or any(not part for part in repository_full_name.split("/"))
        ):
            return Response(
                {
                    "repository_full_name": (
                        "Use the owner/repository format."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        connection = getattr(project, "github_connection", None)
        if connection is None or not connection.installation_id:
            return Response(
                {"detail": "Connect the GitHub App before selecting a repository."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            installation_id = int(connection.installation_id)
            client = GitHubAppClient()
            token_data = client.create_installation_token(installation_id)
            repository = client.get_repository(
                str(token_data["token"]),
                repository_full_name,
            )
        except GitHubConfigurationError:
            return Response(
                {"detail": "GitHub App is not configured on the server."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except GitHubAPIError as exc:
            if exc.status_code == 404:
                return Response(
                    {
                        "detail": (
                            "This repository is not accessible to the installed "
                            "GitHub App."
                        )
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )
            return Response(
                {"detail": "Unable to verify the repository on GitHub."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        if not repository.get("full_name"):
            return Response(
                {"detail": "GitHub returned an invalid repository."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        metadata = dict(connection.metadata or {})
        metadata.update(
            {
                "repository_id": repository.get("id"),
                "repository_node_id": repository.get("node_id"),
                "repository_private": bool(repository.get("private")),
                "repository_visibility": repository.get("visibility") or "",
                "repository_html_url": repository.get("html_url") or "",
                "default_branch": repository.get("default_branch") or "",
            }
        )

        connection.repository_full_name = repository["full_name"]
        connection.connected = True
        connection.metadata = metadata
        connection.save(
            update_fields=[
                "repository_full_name",
                "connected",
                "metadata",
                "updated_at",
            ]
        )

        project.repository_full_name = repository["full_name"]
        project.default_branch = repository.get("default_branch") or ""
        project.save(
            update_fields=[
                "repository_full_name",
                "default_branch",
                "updated_at",
            ]
        )

        AuditLog.objects.create(
            project=project,
            actor=request.user,
            action="github_repository_connected",
            resource_type="GitHubRepository",
            resource_id=str(repository.get("id") or repository["full_name"]),
            metadata={
                "repository_full_name": repository["full_name"],
                "default_branch": repository.get("default_branch") or "",
            },
        )

        return Response(
            {
                "connected": True,
                "project_id": project.id,
                "repository": _serialize_repository(repository),
            },
            status=status.HTTP_200_OK,
        )


class GitHubDisconnectView(APIView):
    permission_classes = (IsAuthenticated,)

    def post(self, request):
        project_id = request.data.get("project_id")
        project = _project_for_user(request, project_id)
        if project is None:
            return Response(
                {"detail": "Project not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        connection = getattr(project, "github_connection", None)
        if connection is None:
            return Response(
                {"connected": False},
                status=status.HTTP_200_OK,
            )

        connection.repository_full_name = ""
        connection.connected = False
        connection.save(
            update_fields=[
                "repository_full_name",
                "connected",
                "updated_at",
            ]
        )

        project.repository_full_name = ""
        project.default_branch = ""
        project.save(
            update_fields=[
                "repository_full_name",
                "default_branch",
                "updated_at",
            ]
        )

        AuditLog.objects.create(
            project=project,
            actor=request.user,
            action="github_repository_disconnected",
            resource_type="GitHubConnection",
            resource_id=str(connection.pk),
            metadata={},
        )

        return Response(
            {"connected": False},
            status=status.HTTP_200_OK,
        )


__all__ = [
    "GitHubConnectionView",
    "GitHubDisconnectView",
    "GitHubInstallCallbackView",
    "GitHubInstallStartView",
    "GitHubRepositoryConnectView",
    "GitHubRepositoryListView",
]
