from __future__ import annotations

import hashlib
import time
from typing import Any, Mapping
from urllib.parse import quote

import jwt
import requests
from django.conf import settings


class GitHubConfigurationError(RuntimeError):
    """Raised when GitHub App configuration is missing or invalid."""


class GitHubAPIError(RuntimeError):
    """Raised when GitHub returns an unsuccessful API response."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code


class GitHubAppClient:
    """Small server-side GitHub App client used by the onboarding flow."""

    def __init__(self) -> None:
        self.api_url = str(
            getattr(
                settings,
                "GITHUB_API_URL",
                "https://api.github.com",
            )
        ).rstrip("/")

        self.api_version = str(
            getattr(
                settings,
                "GITHUB_API_VERSION",
                "2026-03-10",
            )
        )

        self.timeout = float(
            getattr(
                settings,
                "GITHUB_API_TIMEOUT_SECONDS",
                15,
            )
        )

    @staticmethod
    def _required_setting(name: str) -> str:
        value = str(
            getattr(settings, name, "") or ""
        ).strip()

        if not value:
            raise GitHubConfigurationError(
                f"{name} is not configured."
            )

        return value

    @property
    def app_id(self) -> int:
        raw = self._required_setting(
            "GITHUB_APP_ID"
        )

        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise GitHubConfigurationError(
                "GITHUB_APP_ID must be an integer."
            ) from exc

        if value <= 0:
            raise GitHubConfigurationError(
                "GITHUB_APP_ID must be greater than zero."
            )

        return value

    @property
    def app_slug(self) -> str:
        return self._required_setting(
            "GITHUB_APP_SLUG"
        )

    @property
    def client_id(self) -> str:
        return self._required_setting(
            "GITHUB_APP_CLIENT_ID"
        )

    @property
    def client_secret(self) -> str:
        return self._required_setting(
            "GITHUB_APP_CLIENT_SECRET"
        )

    @property
    def private_key(self) -> str:
        value = self._required_setting(
            "GITHUB_APP_PRIVATE_KEY"
        )

        # dotenv commonly stores multiline PEM values as literal
        # \\n escapes. Convert them back before signing the JWT.
        return value.replace("\\n", "\n").strip()

    @property
    def callback_url(self) -> str:
        return self._required_setting(
            "GITHUB_APP_CALLBACK_URL"
        )

    def _headers(
        self,
        authorization: str,
    ) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": authorization,
            "X-GitHub-Api-Version": self.api_version,
            "User-Agent": "API-Analyzer-GitHub-App",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self.api_url}{path}"

        try:
            response = requests.request(
                method,
                url,
                headers=self._headers(authorization),
                params=params,
                json=json,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GitHubAPIError(
                "Unable to reach GitHub API."
            ) from exc

        if not response.ok:
            # Do not expose response bodies because GitHub can include
            # sensitive details in error messages.
            raise GitHubAPIError(
                (
                    "GitHub API request failed with "
                    f"status {response.status_code}."
                ),
                status_code=response.status_code,
            )

        if (
            response.status_code == 204
            or not response.content
        ):
            return {}

        try:
            data = response.json()
        except ValueError as exc:
            raise GitHubAPIError(
                "GitHub API returned an invalid JSON response.",
                status_code=response.status_code,
            ) from exc

        return data

    def app_jwt(self) -> str:
        """Create the short-lived JWT used to authenticate as the GitHub App."""
        now = int(time.time())

        payload = {
            "iat": now - 60,
            "exp": now + (9 * 60),
            "iss": str(self.app_id),
        }

        try:
            token = jwt.encode(
                payload,
                self.private_key,
                algorithm="RS256",
            )
        except Exception as exc:
            raise GitHubConfigurationError(
                "Unable to generate the GitHub App JWT."
            ) from exc

        return str(token)

    def get_installation(
        self,
        installation_id: int,
    ) -> dict[str, Any]:
        """Verify that an installation belongs to this GitHub App."""
        if installation_id <= 0:
            raise GitHubAPIError(
                "Invalid GitHub installation id."
            )

        data = self._request(
            "GET",
            f"/app/installations/{installation_id}",
            authorization=f"Bearer {self.app_jwt()}",
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub installation response is invalid."
            )

        return data

    def exchange_user_code(
        self,
        code: str,
    ) -> dict[str, Any]:
        """Exchange the GitHub App OAuth callback code for a user token."""
        code = code.strip()

        if not code:
            raise GitHubAPIError(
                "GitHub OAuth code is missing."
            )

        try:
            response = requests.post(
                "https://github.com/login/oauth/access_token",
                headers={
                    "Accept": "application/json",
                    "User-Agent": "API-Analyzer-GitHub-App",
                },
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "code": code,
                    "redirect_uri": self.callback_url,
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GitHubAPIError(
                "Unable to reach GitHub OAuth endpoint."
            ) from exc

        if not response.ok:
            raise GitHubAPIError(
                "GitHub OAuth token exchange failed.",
                status_code=response.status_code,
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise GitHubAPIError(
                "GitHub OAuth returned an invalid JSON response.",
                status_code=response.status_code,
            ) from exc

        if (
            not isinstance(data, dict)
            or data.get("error")
        ):
            raise GitHubAPIError(
                "GitHub OAuth authorization was not completed."
            )

        access_token = data.get(
            "access_token"
        )

        if not access_token:
            raise GitHubAPIError(
                "GitHub OAuth did not return an access token."
            )

        return data

    def get_authenticated_user(
        self,
        user_access_token: str,
    ) -> dict[str, Any]:
        data = self._request(
            "GET",
            "/user",
            authorization=(
                f"Bearer {user_access_token}"
            ),
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub user response is invalid."
            )

        return data

    def get_user_installations(
        self,
        user_access_token: str,
    ) -> list[dict[str, Any]]:
        """Get installations visible to the authenticated GitHub user."""
        data = self._request(
            "GET",
            "/user/installations",
            authorization=(
                f"Bearer {user_access_token}"
            ),
            params={
                "per_page": 100,
                "page": 1,
            },
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub installations response is invalid."
            )

        installations = data.get(
            "installations",
            [],
        )

        if not isinstance(installations, list):
            raise GitHubAPIError(
                "GitHub installations response is invalid."
            )

        return [
            item
            for item in installations
            if isinstance(item, dict)
        ]

    def get_user_installation(
        self,
        username: str,
    ) -> dict[str, Any]:
        """
        Get this GitHub App's installation for a specific GitHub user.
        """
        username = str(username or "").strip()

        if not username:
            raise GitHubAPIError(
                "GitHub username is missing."
            )

        encoded_username = quote(
            username,
            safe="",
        )

        data = self._request(
            "GET",
            f"/users/{encoded_username}/installation",
            authorization=f"Bearer {self.app_jwt()}",
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub user installation response is invalid."
            )

        return data

    def verify_user_has_installation(
        self,
        *,
        user_access_token: str,
        installation_id: int,
    ) -> dict[str, Any]:
        installations = self.get_user_installations(
            user_access_token
        )

        for installation in installations:
            try:
                current_id = int(
                    installation.get("id")
                )
            except (TypeError, ValueError):
                continue

            if current_id == installation_id:
                return installation

        raise GitHubAPIError(
            "The authenticated GitHub user does not have "
            "this app installation."
        )

    def create_installation_token(
        self,
        installation_id: int,
    ) -> dict[str, Any]:
        data = self._request(
            "POST",
            (
                f"/app/installations/"
                f"{installation_id}/access_tokens"
            ),
            authorization=(
                f"Bearer {self.app_jwt()}"
            ),
        )

        if (
            not isinstance(data, dict)
            or not data.get("token")
        ):
            raise GitHubAPIError(
                "GitHub did not return an installation access token."
            )

        return data

    def list_installation_repositories(
        self,
        installation_token: str,
        *,
        page: int = 1,
        per_page: int = 100,
    ) -> dict[str, Any]:
        page = max(
            1,
            min(int(page), 1000),
        )

        per_page = max(
            1,
            min(int(per_page), 100),
        )

        data = self._request(
            "GET",
            "/installation/repositories",
            authorization=(
                f"Bearer {installation_token}"
            ),
            params={
                "page": page,
                "per_page": per_page,
            },
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub repositories response is invalid."
            )

        repositories = data.get(
            "repositories",
            [],
        )

        if not isinstance(repositories, list):
            raise GitHubAPIError(
                "GitHub repositories response is invalid."
            )

        data["repositories"] = [
            repo
            for repo in repositories
            if isinstance(repo, dict)
        ]

        return data

    def get_repository(
        self,
        installation_token: str,
        repository_full_name: str,
    ) -> dict[str, Any]:
        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if (
            len(parts) != 2
            or not all(parts)
        ):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        owner, repo = parts

        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}",
            authorization=(
                f"Bearer {installation_token}"
            ),
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub repository response is invalid."
            )

        return data


def hash_install_state(
    state: str,
) -> str:
    return hashlib.sha256(
        state.encode("utf-8")
    ).hexdigest()


__all__ = [
    "GitHubAPIError",
    "GitHubAppClient",
    "GitHubConfigurationError",
    "hash_install_state",
]