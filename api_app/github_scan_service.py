from __future__ import annotations

"""
github_scan_service.py

Read-only repository inspection used during API Analyzer onboarding.

This module does not change the customer's repository. It discovers:
1. an existing OpenAPI/Swagger contract file, and
2. the most likely backend framework/stack from repository metadata.

The result is intentionally framework-agnostic so additional adapters can be
added later without changing the GitHub connection flow.
"""

import json
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Iterable


# High-signal contract filenames. Keep this list small and deterministic.
CONTRACT_FILENAMES = (
    "openapi.json",
    "openapi.yaml",
    "openapi.yml",
    "swagger.json",
    "swagger.yaml",
    "swagger.yml",
)

# Paths/directories that should not influence framework detection.
IGNORED_PREFIXES = (
    ".git/",
    ".github/",
    "node_modules/",
    "vendor/",
    "venv/",
    ".venv/",
    "env/",
    ".env/",
    "__pycache__/",
    "dist/",
    "build/",
    "target/",
)

# Only these small configuration/dependency files are fetched for content
# inspection. We deliberately do not read the whole repository.
MANIFEST_NAMES = (
    "requirements.txt",
    "requirements-dev.txt",
    "pyproject.toml",
    "Pipfile",
    "Pipfile.lock",
    "poetry.lock",
    "package.json",
    "pom.xml",
    "build.gradle",
    "build.gradle.kts",
    "settings.gradle",
    "settings.gradle.kts",
)


@dataclass(frozen=True)
class ContractDetection:
    found: bool
    path: str = ""
    contract_type: str = ""
    confidence: str = "none"


@dataclass(frozen=True)
class FrameworkDetection:
    detected: bool
    name: str = ""
    language: str = ""
    adapter_type: str = ""
    confidence: str = "none"
    evidence: tuple[str, ...] = ()


@dataclass
class RepositoryScanResult:
    repository: str
    default_branch: str
    contract: ContractDetection
    framework: FrameworkDetection
    scanned_files: int
    tree_truncated: bool = False
    setup_required: bool = True
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "repository": self.repository,
            "default_branch": self.default_branch,
            "contract": {
                "found": self.contract.found,
                "path": self.contract.path,
                "type": self.contract.contract_type,
                "confidence": self.contract.confidence,
            },
            "framework": {
                "detected": self.framework.detected,
                "name": self.framework.name,
                "language": self.framework.language,
                "adapter_type": self.framework.adapter_type,
                "confidence": self.framework.confidence,
                "evidence": list(self.framework.evidence),
            },
            "scanned_files": self.scanned_files,
            "tree_truncated": self.tree_truncated,
            "setup_required": self.setup_required,
            "warnings": list(self.warnings),
        }


def _normalize_path(path: str) -> str:
    return posixpath.normpath(
        str(path or "").strip().lstrip("/")
    ).replace("\\", "/")


def _is_ignored(path: str) -> bool:
    path = _normalize_path(path)
    if path in {".git", "node_modules", "vendor", "venv", ".venv", "env"}:
        return True

    for prefix in IGNORED_PREFIXES:
        if path.startswith(prefix):
            return True

    return False


def _path_is_manifest(path: str) -> bool:
    normalized = _normalize_path(path)
    name = posixpath.basename(normalized)
    return name in MANIFEST_NAMES


def _path_is_python(path: str) -> bool:
    return _normalize_path(path).endswith(".py")


def _path_is_java(path: str) -> bool:
    return _normalize_path(path).endswith(".java")


def _path_is_csharp(path: str) -> bool:
    return _normalize_path(path).endswith(".cs")


def _contract_type(path: str) -> str:
    name = posixpath.basename(_normalize_path(path)).lower()
    if name.startswith("openapi."):
        return "openapi"
    if name.startswith("swagger."):
        return "swagger"
    return ""


def _contract_priority(path: str) -> tuple[int, int, str]:
    normalized = _normalize_path(path)
    name = posixpath.basename(normalized).lower()

    # Prefer root-level OpenAPI over nested copies and OpenAPI over Swagger.
    root_penalty = 0 if "/" not in normalized else 10
    name_priority = {
        "openapi.json": 0,
        "openapi.yaml": 1,
        "openapi.yml": 2,
        "swagger.json": 3,
        "swagger.yaml": 4,
        "swagger.yml": 5,
    }.get(name, 99)

    return (
        name_priority + root_penalty,
        len(normalized),
        normalized,
    )


def _find_contract(tree_files: Iterable[str]) -> ContractDetection:
    candidates = []

    for raw_path in tree_files:
        path = _normalize_path(raw_path)
        if _is_ignored(path):
            continue

        name = posixpath.basename(path).lower()
        if name in CONTRACT_FILENAMES:
            candidates.append(path)
            continue

        # Common docs/schema locations using an OpenAPI-like filename.
        if (
            name.startswith("openapi.")
            or name.startswith("swagger.")
        ):
            candidates.append(path)

    if not candidates:
        return ContractDetection(found=False)

    selected = sorted(
        set(candidates),
        key=_contract_priority,
    )[0]

    return ContractDetection(
        found=True,
        path=selected,
        contract_type=_contract_type(selected),
        confidence="high" if posixpath.dirname(selected) == "" else "medium",
    )


def _lower(text: str) -> str:
    return str(text or "").lower()


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    lowered = _lower(text)
    return any(needle in lowered for needle in needles)


def _detect_framework(
    tree_paths: list[str],
    manifest_contents: dict[str, str],
) -> FrameworkDetection:
    paths = {_normalize_path(path) for path in tree_paths}
    names = {
        posixpath.basename(path).lower()
        for path in paths
    }

    evidence: list[str] = []

    python_manifests = [
        content
        for path, content in manifest_contents.items()
        if posixpath.basename(path).lower()
        in {
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "pipfile",
            "poetry.lock",
            "pipfile.lock",
        }
    ]
    python_text = "\n".join(python_manifests)

    # Django/DRF first because DRF is a direct API framework signal.
    if (
        "manage.py" in names
        or _contains_any(
            python_text,
            (
                "django",
                "djangorestframework",
                "rest_framework",
                "drf",
            ),
        )
    ):
        if _contains_any(
            python_text,
            (
                "djangorestframework",
                "rest_framework",
                "drf",
            ),
        ):
            evidence.append("Django REST Framework dependency detected.")
            adapter = "django-rest-framework"
            framework_name = "Django REST Framework"
        else:
            evidence.append("Django project marker detected.")
            adapter = "django"
            framework_name = "Django"

        return FrameworkDetection(
            detected=True,
            name=framework_name,
            language="Python",
            adapter_type=adapter,
            confidence="high",
            evidence=tuple(evidence),
        )

    if _contains_any(
        python_text,
        (
            "fastapi",
        ),
    ):
        evidence.append("FastAPI dependency detected.")
        return FrameworkDetection(
            detected=True,
            name="FastAPI",
            language="Python",
            adapter_type="fastapi",
            confidence="high",
            evidence=tuple(evidence),
        )

    if _contains_any(
        python_text,
        (
            "flask",
        ),
    ):
        evidence.append("Flask dependency detected.")
        return FrameworkDetection(
            detected=True,
            name="Flask",
            language="Python",
            adapter_type="flask",
            confidence="high",
            evidence=tuple(evidence),
        )

    package_json = manifest_contents.get(
        next(
            (
                path
                for path in manifest_contents
                if posixpath.basename(path).lower() == "package.json"
            ),
            "",
        ),
        "",
    )

    if package_json:
        try:
            package = json.loads(package_json)
        except json.JSONDecodeError:
            package = {}

        package_text = json.dumps(
            package,
            ensure_ascii=False,
        ).lower()

        dependencies = package.get("dependencies") or {}
        dev_dependencies = package.get("devDependencies") or {}
        dependency_names = {
            str(name).lower()
            for name in {
                **(
                    dependencies
                    if isinstance(dependencies, dict)
                    else {}
                ),
                **(
                    dev_dependencies
                    if isinstance(dev_dependencies, dict)
                    else {}
                ),
            }
        }

        if "@nestjs/core" in dependency_names:
            evidence.append("NestJS package detected.")
            return FrameworkDetection(
                detected=True,
                name="NestJS",
                language="TypeScript/JavaScript",
                adapter_type="nestjs",
                confidence="high",
                evidence=tuple(evidence),
            )

        if "express" in dependency_names or '"express"' in package_text:
            evidence.append("Express package detected.")
            return FrameworkDetection(
                detected=True,
                name="Express",
                language="JavaScript/TypeScript",
                adapter_type="express",
                confidence="high",
                evidence=tuple(evidence),
            )

    pom = manifest_contents.get(
        next(
            (
                path
                for path in manifest_contents
                if posixpath.basename(path).lower() == "pom.xml"
            ),
            "",
        ),
        "",
    )
    gradle = "\n".join(
        content
        for path, content in manifest_contents.items()
        if posixpath.basename(path).lower()
        in {
            "build.gradle",
            "build.gradle.kts",
            "settings.gradle",
            "settings.gradle.kts",
        }
    )

    java_text = f"{pom}\n{gradle}"
    if _contains_any(
        java_text,
        (
            "spring-boot",
            "org.springframework.boot",
        ),
    ):
        evidence.append("Spring Boot build configuration detected.")
        return FrameworkDetection(
            detected=True,
            name="Spring Boot",
            language="Java",
            adapter_type="spring-boot",
            confidence="high",
            evidence=tuple(evidence),
        )

    csproj_paths = [
        path
        for path in paths
        if path.lower().endswith(".csproj")
    ]

    if csproj_paths:
        # We only read manifests intentionally; a .csproj file itself is
        # enough to identify an ASP.NET-capable .NET repository.
        evidence.append(".csproj project file detected.")
        return FrameworkDetection(
            detected=True,
            name="ASP.NET / .NET",
            language="C#",
            adapter_type="aspnet",
            confidence="medium",
            evidence=tuple(evidence),
        )

    # Generic Python project marker. This is intentionally below known
    # API frameworks so the UI can say "Python (framework not determined)".
    if (
        python_manifests
        or any(_path_is_python(path) for path in paths)
    ):
        evidence.append("Python project markers detected.")
        return FrameworkDetection(
            detected=True,
            name="Python",
            language="Python",
            adapter_type="python",
            confidence="low",
            evidence=tuple(evidence),
        )

    if any(
        _normalize_path(path).lower().endswith(
            (".js", ".jsx", ".ts", ".tsx")
        )
        for path in paths
    ):
        evidence.append("JavaScript/TypeScript source files detected.")
        return FrameworkDetection(
            detected=True,
            name="Node.js / JavaScript",
            language="JavaScript/TypeScript",
            adapter_type="node",
            confidence="low",
            evidence=tuple(evidence),
        )

    if any(_path_is_java(path) for path in paths):
        evidence.append("Java source files detected.")
        return FrameworkDetection(
            detected=True,
            name="Java",
            language="Java",
            adapter_type="java",
            confidence="low",
            evidence=tuple(evidence),
        )

    if any(_path_is_csharp(path) for path in paths):
        evidence.append("C# source files detected.")
        return FrameworkDetection(
            detected=True,
            name="C# / .NET",
            language="C#",
            adapter_type="dotnet",
            confidence="low",
            evidence=tuple(evidence),
        )

    return FrameworkDetection(detected=False)


class GitHubRepositoryScanner:
    """
    Read-only scanner for a selected GitHub repository.

    The caller supplies the already-authenticated GitHubAppClient and
    installation token. No credentials are stored or returned by this class.
    """

    def __init__(
        self,
        github_client: Any,
        installation_token: str,
    ) -> None:
        self.client = github_client
        self.installation_token = str(installation_token)

    def _candidate_manifest_paths(
        self,
        tree_paths: Iterable[str],
    ) -> list[str]:
        candidates = []

        for raw_path in tree_paths:
            path = _normalize_path(raw_path)

            if _is_ignored(path):
                continue

            if _path_is_manifest(path):
                candidates.append(path)

            if (
                posixpath.basename(path).lower().endswith(
                    ".csproj"
                )
            ):
                candidates.append(path)

        # Avoid downloading an arbitrary number of manifests in a monorepo.
        return sorted(set(candidates))[:20]

    def scan(
        self,
        repository_full_name: str,
        *,
        default_branch: str | None = None,
    ) -> RepositoryScanResult:
        repository = self.client.get_repository(
            self.installation_token,
            repository_full_name,
        )

        resolved_branch = str(
            default_branch
            or repository.get("default_branch")
            or "main"
        ).strip()

        tree_data = self.client.get_repository_tree(
            self.installation_token,
            repository_full_name,
            tree_ref=resolved_branch,
            recursive=True,
        )

        tree_items = tree_data.get("tree", [])
        file_paths = [
            _normalize_path(item.get("path"))
            for item in tree_items
            if item.get("type") == "blob"
            and item.get("path")
            and not _is_ignored(item.get("path"))
        ]

        contract = _find_contract(file_paths)

        manifest_contents: dict[str, str] = {}
        for path in self._candidate_manifest_paths(file_paths):
            try:
                file_data = self.client.get_repository_file(
                    self.installation_token,
                    repository_full_name,
                    path,
                    ref=resolved_branch,
                )
            except Exception:
                # A single inaccessible/oversized manifest should not make
                # onboarding fail. The view can still show the contract result.
                continue

            content = str(file_data.get("content") or "")
            if content:
                manifest_contents[path] = content

        framework = _detect_framework(
            file_paths,
            manifest_contents,
        )

        warnings: list[str] = []
        if tree_data.get("truncated"):
            warnings.append(
                "GitHub returned a truncated repository tree. "
                "Detection may be incomplete for very large repositories."
            )

        if not contract.found:
            warnings.append(
                "No standard OpenAPI or Swagger contract file was detected."
            )

        if not framework.detected:
            warnings.append(
                "The backend framework could not be determined automatically."
            )

        return RepositoryScanResult(
            repository=str(
                repository.get("full_name")
                or repository_full_name
            ),
            default_branch=resolved_branch,
            contract=contract,
            framework=framework,
            scanned_files=len(file_paths),
            tree_truncated=bool(tree_data.get("truncated")),
            setup_required=True,
            warnings=warnings,
        )


__all__ = [
    "ContractDetection",
    "FrameworkDetection",
    "GitHubRepositoryScanner",
    "RepositoryScanResult",
]
