from __future__ import annotations

import base64
import hashlib
import re
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

    def get_branch_sha(
        self,
        installation_token: str,
        repository_full_name: str,
        branch_name: str,
    ) -> str:
        """Return the exact commit SHA currently pointed to by a branch."""
        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        branch_name = str(branch_name or "").strip()
        if not branch_name:
            raise GitHubAPIError("branch_name is required.")
        if "\r" in branch_name or "\n" in branch_name:
            raise GitHubAPIError("branch_name cannot contain newlines.")

        owner, repo = parts

        data = self._request(
            "GET",
            (
                f"/repos/{owner}/{repo}/git/ref/heads/"
                f"{quote(branch_name, safe='/')}"
            ),
            authorization=(
                f"Bearer {installation_token}"
            ),
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub branch reference response is invalid."
            )

        object_data = data.get("object") or {}
        if not isinstance(object_data, dict):
            raise GitHubAPIError(
                "GitHub branch reference response is invalid."
            )

        sha = str(object_data.get("sha") or "").strip()
        if not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise GitHubAPIError(
                "GitHub branch reference did not contain a valid commit SHA."
            )

        return sha

    def get_repository_tree(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        tree_ref: str,
        recursive: bool = True,
    ) -> dict[str, Any]:
        """
        Read the repository Git tree for a branch/tag/commit ref.

        The scanner uses this endpoint instead of recursively walking
        directory-by-directory because GitHub's Contents API is limited
        to 1,000 entries per directory.
        """
        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        tree_ref = str(tree_ref or "").strip()
        if not tree_ref:
            raise GitHubAPIError("tree_ref is required.")

        owner, repo = parts

        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{quote(tree_ref, safe='')}",
            authorization=(
                f"Bearer {installation_token}"
            ),
            params={
                "recursive": "1" if recursive else None,
            },
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub repository tree response is invalid."
            )

        tree = data.get("tree", [])
        if not isinstance(tree, list):
            raise GitHubAPIError(
                "GitHub repository tree response is invalid."
            )

        data["tree"] = [
            item
            for item in tree
            if isinstance(item, dict)
        ]

        return data

    def get_repository_file(
        self,
        installation_token: str,
        repository_full_name: str,
        path: str,
        *,
        ref: str | None = None,
    ) -> dict[str, Any]:
        """
        Read one repository file through GitHub's Contents API.

        The method returns both the GitHub metadata and decoded UTF-8
        text when GitHub provides the file as base64 content.
        """
        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        path = str(path or "").strip().lstrip("/")
        if not path:
            raise GitHubAPIError("Repository file path is required.")

        owner, repo = parts

        params = {}
        if ref:
            params["ref"] = str(ref).strip()

        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/contents/{quote(path, safe='/')}",
            authorization=(
                f"Bearer {installation_token}"
            ),
            params=params or None,
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub repository file response is invalid."
            )

        if data.get("type") != "file":
            raise GitHubAPIError(
                "GitHub repository path is not a file."
            )

        raw_content = data.get("content") or ""
        encoding = str(data.get("encoding") or "").lower()

        decoded_text = ""
        if raw_content and encoding == "base64":
            try:
                decoded_text = base64.b64decode(
                    raw_content,
                    validate=False,
                ).decode(
                    "utf-8",
                    errors="replace",
                )
            except (ValueError, TypeError):
                raise GitHubAPIError(
                    "GitHub returned invalid file content."
                )

        return {
            "path": data.get("path") or path,
            "sha": data.get("sha") or "",
            "size": data.get("size"),
            "html_url": data.get("html_url") or "",
            "encoding": encoding,
            "content": decoded_text,
            "download_url": data.get("download_url") or "",
        }
    def create_branch(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        branch_name: str,
        from_sha: str | None = None,
        from_branch: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a new branch from an exact commit SHA or an existing branch.

        The method never overwrites an existing branch.
        """

        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        branch_name = (
            str(branch_name or "")
            .strip()
            .removeprefix("refs/heads/")
        )

        if not branch_name:
            raise GitHubAPIError(
                "branch_name is required."
            )

        if branch_name.startswith("/") or ".." in branch_name:
            raise GitHubAPIError(
                "Invalid branch_name."
            )

        from_sha = str(from_sha or "").strip()
        from_branch = str(from_branch or "").strip()

        if bool(from_sha) == bool(from_branch):
            raise GitHubAPIError(
                "Provide exactly one of from_sha or from_branch."
            )

        owner, repo = parts

        source_sha = from_sha

        if from_branch:
            ref_data = self._request(
                "GET",
                (
                    f"/repos/{owner}/{repo}/git/ref/heads/"
                    f"{quote(from_branch, safe='/')}"
                ),
                authorization=(
                    f"Bearer {installation_token}"
                ),
            )

            if not isinstance(ref_data, dict):
                raise GitHubAPIError(
                    "GitHub branch reference response is invalid."
                )

            object_data = ref_data.get("object") or {}

            if not isinstance(object_data, dict):
                raise GitHubAPIError(
                    "GitHub branch reference response is invalid."
                )

            source_sha = str(
                object_data.get("sha") or ""
            ).strip()

            if not source_sha:
                raise GitHubAPIError(
                    "GitHub branch reference does not contain a SHA."
                )

        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            authorization=(
                f"Bearer {installation_token}"
            ),
            json={
                "ref": f"refs/heads/{branch_name}",
                "sha": source_sha,
            },
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub branch creation response is invalid."
            )

        return data

    def delete_branch(
        self,
        installation_token: str,
        repository_full_name: str,
        branch_name: str,
    ) -> None:
        """Delete a repository branch by name."""
        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        branch_name = (
            str(branch_name or "")
            .strip()
            .removeprefix("refs/heads/")
        )

        if (
            not branch_name
            or branch_name.startswith("/")
            or ".." in branch_name
            or "\r" in branch_name
            or "\n" in branch_name
        ):
            raise GitHubAPIError("Invalid branch_name.")

        owner, repo = parts
        self._request(
            "DELETE",
            (
                f"/repos/{owner}/{repo}/git/refs/heads/"
                f"{quote(branch_name, safe='/')}"
            ),
            authorization=(
                f"Bearer {installation_token}"
            ),
        )

    def create_or_update_repository_file(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        path: str,
        content: str,
        branch: str,
        commit_message: str,
    ) -> dict[str, Any]:
        """
        Create or update one repository file on a branch.

        Existing files are updated using their current Git blob SHA.
        New files are created without a SHA.
        """

        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        path = str(path or "").strip().lstrip("/")

        if not path:
            raise GitHubAPIError(
                "Repository file path is required."
            )

        branch = str(branch or "").strip()

        if not branch:
            raise GitHubAPIError(
                "branch is required."
            )

        commit_message = str(
            commit_message or ""
        ).strip()

        if not commit_message:
            raise GitHubAPIError(
                "commit_message is required."
            )

        if not isinstance(content, str):
            raise GitHubAPIError(
                "Repository file content must be text."
            )

        owner, repo = parts

        existing_sha: str | None = None

        try:
            existing = self.get_repository_file(
                installation_token,
                repository_full_name,
                path,
                ref=branch,
            )

            existing_sha = str(
                existing.get("sha") or ""
            ).strip() or None

        except GitHubAPIError as exc:
            if exc.status_code != 404:
                raise

        encoded_content = base64.b64encode(
            content.encode("utf-8")
        ).decode("ascii")

        payload: dict[str, Any] = {
            "message": commit_message,
            "content": encoded_content,
            "branch": branch,
        }

        if existing_sha:
            payload["sha"] = existing_sha

        data = self._request(
            "PUT",
            (
                f"/repos/{owner}/{repo}/contents/"
                f"{quote(path, safe='/')}"
            ),
            authorization=(
                f"Bearer {installation_token}"
            ),
            json=payload,
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub file write response is invalid."
            )

        return data

    def create_pull_request(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
    ) -> dict[str, Any]:
        """
        Create a pull request from head into base.
        """

        parts = (
            repository_full_name
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        title = str(title or "").strip()
        head = str(head or "").strip()
        base = str(base or "").strip()
        body = str(body or "")

        if not title:
            raise GitHubAPIError(
                "Pull request title is required."
            )

        if not head:
            raise GitHubAPIError(
                "Pull request head is required."
            )

        if not base:
            raise GitHubAPIError(
                "Pull request base is required."
            )

        owner, repo = parts

        data = self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls",
            authorization=(
                f"Bearer {installation_token}"
            ),
            json={
                "title": title,
                "head": head,
                "base": base,
                "body": body,
            },
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub pull request response is invalid."
            )

        return data

    @staticmethod
    def _validate_actions_name(
        name: str,
        *,
        kind: str,
    ) -> str:
        """
        Validate a GitHub Actions secret or variable name.
        """
        normalized = str(name or "").strip()

        if not normalized:
            raise GitHubAPIError(
                f"GitHub Actions {kind} name is required."
            )

        if normalized.startswith("GITHUB_"):
            raise GitHubAPIError(
                f"GitHub Actions {kind} name cannot start with GITHUB_."
            )

        if not all(
            character.isascii()
            and (
                character.isalnum()
                or character == "_"
            )
            for character in normalized
        ):
            raise GitHubAPIError(
                f"GitHub Actions {kind} name may contain only "
                "ASCII letters, numbers, and underscores."
            )

        return normalized

    @staticmethod
    def _repository_parts(
        repository_full_name: str,
    ) -> tuple[str, str]:
        parts = (
            str(repository_full_name or "")
            .strip()
            .split("/", 1)
        )

        if len(parts) != 2 or not all(parts):
            raise GitHubAPIError(
                "repository_full_name must use the "
                "owner/repository format."
            )

        return parts[0], parts[1]

    def get_actions_public_key(
        self,
        installation_token: str,
        repository_full_name: str,
    ) -> dict[str, str]:
        """
        Get the repository Actions public key required for encrypting
        repository-level Actions secrets.
        """
        owner, repo = self._repository_parts(
            repository_full_name
        )

        data = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/secrets/public-key",
            authorization=f"Bearer {installation_token}",
        )

        if not isinstance(data, dict):
            raise GitHubAPIError(
                "GitHub Actions public-key response is invalid."
            )

        key = str(
            data.get("key") or ""
        ).strip()

        key_id = str(
            data.get("key_id") or ""
        ).strip()

        if not key or not key_id:
            raise GitHubAPIError(
                "GitHub Actions public-key response is incomplete."
            )

        return {
            "key": key,
            "key_id": key_id,
        }

    @staticmethod
    def _encrypt_actions_secret(
        public_key: str,
        secret_value: str,
    ) -> str:
        """
        Encrypt a GitHub Actions secret using the sealed-box primitive
        expected by GitHub's Actions secrets API.

        PyNaCl is imported lazily so existing GitHub operations do not
        require this dependency until a repository secret is configured.
        """
        try:
            from nacl.public import (
                PublicKey,
                SealedBox,
            )
        except ImportError as exc:
            raise GitHubConfigurationError(
                "PyNaCl is required to configure GitHub Actions secrets. "
                "Install it with: pip install pynacl."
            ) from exc

        try:
            public_key_bytes = base64.b64decode(
                str(public_key).encode("ascii"),
                validate=True,
            )

            encrypted = SealedBox(
                PublicKey(public_key_bytes)
            ).encrypt(
                str(secret_value).encode("utf-8")
            )

            return base64.b64encode(
                encrypted
            ).decode("ascii")

        except (ValueError, TypeError) as exc:
            raise GitHubAPIError(
                "GitHub Actions public key is invalid."
            ) from exc

    def create_or_update_actions_secret(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        secret_name: str,
        secret_value: str,
    ) -> dict[str, Any]:
        """
        Create or replace one repository-level GitHub Actions secret.

        The plaintext secret is never returned by this method.
        """
        name = self._validate_actions_name(
            secret_name,
            kind="secret",
        )

        if secret_value is None:
            raise GitHubAPIError(
                "GitHub Actions secret value is required."
            )

        if not isinstance(secret_value, str):
            raise GitHubAPIError(
                "GitHub Actions secret value must be text."
            )

        repository_key = self.get_actions_public_key(
            installation_token,
            repository_full_name,
        )

        encrypted_value = self._encrypt_actions_secret(
            repository_key["key"],
            secret_value,
        )

        owner, repo = self._repository_parts(
            repository_full_name
        )

        self._request(
            "PUT",
            (
                f"/repos/{owner}/{repo}/actions/secrets/"
                f"{quote(name, safe='')}"
            ),
            authorization=f"Bearer {installation_token}",
            json={
                "encrypted_value": encrypted_value,
                "key_id": repository_key["key_id"],
            },
        )

        return {
            "name": name,
            "configured": True,
        }

    def create_or_update_actions_variable(
        self,
        installation_token: str,
        repository_full_name: str,
        *,
        variable_name: str,
        value: str,
    ) -> dict[str, Any]:
        """
        Create or update one repository-level GitHub Actions variable.

        Existing variables use PATCH. If GitHub reports 404, the method
        creates the variable with POST.
        """
        name = self._validate_actions_name(
            variable_name,
            kind="variable",
        )

        if value is None:
            raise GitHubAPIError(
                "GitHub Actions variable value is required."
            )

        if not isinstance(value, str):
            raise GitHubAPIError(
                "GitHub Actions variable value must be text."
            )

        owner, repo = self._repository_parts(
            repository_full_name
        )

        variable_path = (
            f"/repos/{owner}/{repo}/actions/variables/"
            f"{quote(name, safe='')}"
        )

        try:
            data = self._request(
                "PATCH",
                variable_path,
                authorization=f"Bearer {installation_token}",
                json={
                    "name": name,
                    "value": value,
                },
            )
        except GitHubAPIError as exc:
            if exc.status_code != 404:
                raise

            data = self._request(
                "POST",
                f"/repos/{owner}/{repo}/actions/variables",
                authorization=f"Bearer {installation_token}",
                json={
                    "name": name,
                    "value": value,
                },
            )

            return {
                "name": name,
                "configured": True,
                "created": True,
                "response": (
                    data
                    if isinstance(data, dict)
                    else {}
                ),
            }

        return {
            "name": name,
            "configured": True,
            "created": False,
            "response": (
                data
                if isinstance(data, dict)
                else {}
            ),
        }


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
