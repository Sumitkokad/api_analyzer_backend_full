from __future__ import annotations

"""
Django REST Framework contract adapter.

This adapter is the first concrete framework integration for API Analyzer.

Important architecture:

    Repository Scanner
            ↓
    Django REST Framework Adapter
            ↓
    OpenAPI contract acquisition
            ↓
    Common Analyzer
            ↓
    Semantic Diff + Deterministic Rules

This adapter does NOT classify breaking changes.

It only:
    - detects Django REST Framework repositories
    - determines whether automated contract generation is possible
    - produces a deterministic generation plan
    - validates the basic OpenAPI contract structure
    - describes the contract source

CodeForge is a test/integration repository, not a hardcoded repository
restriction. This adapter must work with any repository that provides
sufficient Django REST Framework evidence.
"""

import posixpath
import re
import shlex
from collections.abc import Iterable, Mapping
from pathlib import PurePosixPath
from typing import Any

from .base import (
    AdapterDetectionResult,
    ContractAdapter,
    ContractGenerationPlan,
    ContractSourceDescription,
    ContractValidationResult,
)


# ============================================================================
# Constants
# ============================================================================


ADAPTER_TYPE = "django-rest-framework"

ADAPTER_VERSION = "1.0.0"

FRAMEWORK_NAME = "Django REST Framework"

LANGUAGE = "Python"

PACKAGE_MANAGER = "pip"

DEFAULT_OUTPUT_PATH = "openapi.json"

DEFAULT_SCHEMA_COMMAND = (
    "python manage.py spectacular "
    "--file openapi.json "
    "--validate"
)

# Strong dependency markers for DRF.
DRF_DEPENDENCY_NAMES = (
    "djangorestframework",
    "rest-framework",
)

# Import/configuration markers that can appear in Python dependency/config
# files. These are intentionally small and deterministic.
DRF_IMPORT_MARKERS = (
    "rest_framework",
    "from rest_framework",
    "import rest_framework",
)

DJANGO_MARKERS = (
    "django",
    "django-admin",
    "django.conf",
    "django.urls",
)

MANAGE_FILE = "manage.py"

SUPPORTED_OPENAPI_PREFIXES = (
    "3.",
)

SUPPORTED_SWAGGER_VERSION = "2.0"

SHA256_PATTERN = re.compile(
    r"^[0-9a-fA-F]{40,64}$"
)


# ============================================================================
# Utility helpers
# ============================================================================


def _normalize_path(path: Any) -> str:
    """
    Normalize a repository path to a safe POSIX-style path.

    GitHub repository paths use forward slashes regardless of the local
    operating system used by API Analyzer.
    """

    value = str(path or "").strip().replace("\\", "/")

    if not value:
        return ""

    value = value.lstrip("/")

    normalized = str(
        PurePosixPath(value)
    )

    if normalized == ".":
        return ""

    return normalized


def _path_basename(path: Any) -> str:
    normalized = _normalize_path(path)

    if not normalized:
        return ""

    return PurePosixPath(normalized).name.lower()


def _path_directory(path: Any) -> str:
    normalized = _normalize_path(path)

    if not normalized:
        return ""

    parent = PurePosixPath(normalized).parent

    if str(parent) == ".":
        return ""

    return str(parent)


def _paths_from_repository(
    repository: Mapping[str, Any],
) -> tuple[str, ...]:
    """
    Extract repository file paths from the normalized scanner payload.

    The scanner can evolve without forcing every adapter to depend on one
    temporary field name. We support a few safe aliases.
    """

    candidate_keys = (
        "tree_paths",
        "file_paths",
        "scanned_paths",
        "paths",
        "files",
    )

    raw_items: Any = None

    for key in candidate_keys:
        if key in repository:
            raw_items = repository.get(key)
            break

    if raw_items is None:
        return ()

    if isinstance(raw_items, Mapping):
        raw_items = raw_items.keys()

    if not isinstance(raw_items, Iterable) or isinstance(
        raw_items,
        (str, bytes),
    ):
        return ()

    normalized: set[str] = set()

    for item in raw_items:
        if isinstance(item, Mapping):
            value = (
                item.get("path")
                or item.get("name")
                or ""
            )
        else:
            value = item

        path = _normalize_path(value)

        if path:
            normalized.add(path)

    return tuple(
        sorted(normalized)
    )


def _manifest_contents_from_repository(
    repository: Mapping[str, Any],
) -> dict[str, str]:
    """
    Extract manifest contents from the scanner payload.

    Expected shape:

        {
            "requirements.txt": "...",
            "pyproject.toml": "...",
        }

    Unknown entries are ignored.
    """

    raw = repository.get(
        "manifest_contents"
    )

    if not isinstance(raw, Mapping):
        return {}

    result: dict[str, str] = {}

    for raw_path, raw_content in raw.items():
        path = _normalize_path(raw_path)

        if not path:
            continue

        if raw_content is None:
            continue

        result[path] = str(
            raw_content
        )

    return result


def _source_contents_from_repository(
    repository: Mapping[str, Any],
) -> dict[str, str]:
    """
    Optional source-content extraction.

    The current scanner intentionally reads only a small set of manifests,
    but allowing source snippets here makes the adapter compatible with a
    future scanner that can provide targeted source evidence.
    """

    raw = repository.get(
        "source_contents"
    )

    if not isinstance(raw, Mapping):
        return {}

    result: dict[str, str] = {}

    for raw_path, raw_content in raw.items():
        path = _normalize_path(raw_path)

        if not path:
            continue

        if raw_content is None:
            continue

        result[path] = str(
            raw_content
        )

    return result


def _combined_python_text(
    repository: Mapping[str, Any],
) -> str:
    """
    Build one deterministic searchable text blob from available Python
    project evidence.
    """

    manifests = _manifest_contents_from_repository(
        repository
    )

    sources = _source_contents_from_repository(
        repository
    )

    parts = [
        content
        for path, content in {
            **manifests,
            **sources,
        }.items()
        if (
            path.lower().endswith(
                (
                    ".txt",
                    ".toml",
                    ".py",
                    ".cfg",
                    ".ini",
                    ".lock",
                )
            )
            or _path_basename(path)
            in {
                "requirements.txt",
                "requirements-dev.txt",
                "pyproject.toml",
                "pipfile",
                "pipfile.lock",
                "poetry.lock",
                "setup.cfg",
                "setup.py",
            }
        )
    ]

    return "\n".join(parts).lower()


def _has_drf_dependency(
    text: str,
) -> bool:
    lowered = str(text or "").lower()

    return any(
        marker in lowered
        for marker in DRF_DEPENDENCY_NAMES
    )


def _has_drf_import(
    text: str,
) -> bool:
    lowered = str(text or "").lower()

    return any(
        marker in lowered
        for marker in DRF_IMPORT_MARKERS
    )


def _has_django_marker(
    text: str,
) -> bool:
    lowered = str(text or "").lower()

    return any(
        marker in lowered
        for marker in DJANGO_MARKERS
    )


def _has_manage_py(
    paths: Iterable[str],
) -> bool:
    return any(
        _path_basename(path) == MANAGE_FILE
        for path in paths
    )


def _find_manage_py(
    paths: Iterable[str],
) -> str:
    candidates = sorted(
        {
            _normalize_path(path)
            for path in paths
            if _path_basename(path) == MANAGE_FILE
        }
    )

    return candidates[0] if candidates else ""


def _dependency_files_for_repository(
    repository: Mapping[str, Any],
    *,
    working_directory: str,
) -> tuple[str, ...]:
    """Return supported Python dependency files in deterministic priority order."""

    paths = set(_paths_from_repository(repository))
    manifests = set(_manifest_contents_from_repository(repository))
    candidates = paths | manifests

    priority = {
        "requirements.txt": 0,
        "requirements-dev.txt": 1,
        "pyproject.toml": 2,
        "setup.py": 3,
        "setup.cfg": 4,
    }

    def dependency_priority(path: str) -> tuple[int, int, str]:
        normalized = _normalize_path(path)
        basename = _path_basename(normalized)
        if basename == "requirements.txt":
            file_priority = priority["requirements.txt"]
        elif basename.startswith("requirements-") and basename.endswith(".txt"):
            file_priority = 1
        else:
            file_priority = priority.get(basename, 99)

        workdir = _normalize_path(working_directory)
        if workdir:
            in_workdir = normalized == workdir or normalized.startswith(f"{workdir}/")
        else:
            in_workdir = "/" not in normalized

        return (0 if in_workdir else 1, file_priority, normalized)

    def is_supported(path: str) -> bool:
        basename = _path_basename(path)
        return (
            basename.startswith("requirements")
            and basename.endswith(".txt")
        ) or basename in {
            "pyproject.toml",
            "setup.py",
            "setup.cfg",
        }

    selected = [path for path in candidates if is_supported(path)]
    return tuple(sorted(set(selected), key=dependency_priority))


def _install_command_for_dependency(
    dependency_file: str,
    *,
    working_directory: str,
    install_drf_spectacular: bool,
) -> str:
    """Build a pip install command relative to the adapter working directory."""

    dependency_file = _normalize_path(dependency_file)
    working_directory = _normalize_path(working_directory) or "."
    relative_dependency = posixpath.relpath(
        dependency_file,
        working_directory,
    )

    basename = _path_basename(dependency_file)

    if basename.startswith("requirements") and basename.endswith(".txt"):
        command = f"python -m pip install -r {relative_dependency}"
    elif basename in {"pyproject.toml", "setup.py", "setup.cfg"}:
        dependency_directory = PurePosixPath(dependency_file).parent
        relative_project = posixpath.relpath(
            str(dependency_directory),
            working_directory,
        )
        if relative_project == ".":
            relative_project = "."
        command = (
            "python -m pip install "
            f"{shlex.quote(relative_project)}"
        )
    else:
        command = ""

    if install_drf_spectacular:
        command = (
            f"{command} && " if command else ""
        ) + "python -m pip install drf-spectacular"

    return command


def _drf_spectacular_present(
    repository: Mapping[str, Any],
) -> bool:
    manifest_text = "\n".join(
        _manifest_contents_from_repository(repository).values()
    ).lower()

    return (
        "drf-spectacular" in manifest_text
        or "drf_spectacular" in manifest_text
    )


def _validate_commit_sha(
    commit_sha: str,
) -> str:
    value = str(
        commit_sha or ""
    ).strip()

    if not value:
        raise ValueError(
            "commit_sha is required for contract generation."
        )

    if not SHA256_PATTERN.fullmatch(value):
        raise ValueError(
            "commit_sha must be a valid Git commit SHA."
        )

    return value


def _contract_from_repository(
    repository: Mapping[str, Any],
) -> Mapping[str, Any]:
    """
    Return the scanner's contract metadata when available.
    """

    raw = repository.get(
        "contract"
    )

    if isinstance(raw, Mapping):
        return raw

    return {}


def _existing_contract_path(
    repository: Mapping[str, Any],
) -> str:
    contract = _contract_from_repository(
        repository
    )

    path = contract.get(
        "path"
    )

    return _normalize_path(
        path
    )


def _framework_hint(
    repository: Mapping[str, Any],
) -> str:
    framework = repository.get(
        "framework"
    )

    if not isinstance(
        framework,
        Mapping,
    ):
        return ""

    return str(
        framework.get("adapter_type")
        or ""
    ).strip().lower()


def _safe_reason(
    value: Any,
) -> str:
    return str(
        value or ""
    ).strip()


# ============================================================================
# Django REST Framework Adapter
# ============================================================================


class DjangoRESTFrameworkAdapter(
    ContractAdapter
):
    """
    Contract adapter for Django REST Framework.

    Detection requires actual DRF evidence. A repository containing only
    Django without DRF is NOT automatically claimed by this adapter.

    Generation uses drf-spectacular as the standard OpenAPI generator.

    The setup layer is responsible for ensuring that drf-spectacular is
    installed and configured before executing the generated command.

    drf-spectacular documents the following CI-oriented command pattern:

        ./manage.py spectacular
            --file schema.yaml
            --validate

    The adapter therefore uses the equivalent repository-local command:

        python manage.py spectacular --file openapi.json --validate
    """

    # ------------------------------------------------------------------
    # Stable identity
    # ------------------------------------------------------------------

    @property
    def adapter_type(self) -> str:
        return ADAPTER_TYPE

    @property
    def framework_name(self) -> str:
        return FRAMEWORK_NAME

    @property
    def language(self) -> str:
        return LANGUAGE

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect(
        self,
        repository: Mapping[str, Any],
    ) -> AdapterDetectionResult:
        """
        Detect Django REST Framework from deterministic repository evidence.

        Required strong combination:

            Django project marker/manage.py
                    +
            DRF dependency or DRF import evidence

        A scanner-provided DRF adapter hint is accepted only as additional
        evidence and never by itself.

        This prevents accidental detection of DRF simply because an arbitrary
        repository field says so.
        """

        if not isinstance(
            repository,
            Mapping,
        ):
            return AdapterDetectionResult(
                detected=False,
                adapter_type=self.adapter_type,
                framework_name=self.framework_name,
                language=self.language,
                confidence="none",
                reason="Repository metadata is invalid.",
            )

        paths = _paths_from_repository(
            repository
        )

        python_text = _combined_python_text(
            repository
        )

        manage_py_found = _has_manage_py(
            paths
        )

        django_found = (
            manage_py_found
            or _has_django_marker(
                python_text
            )
        )

        drf_dependency_found = _has_drf_dependency(
            python_text
        )

        drf_import_found = _has_drf_import(
            python_text
        )

        hint = _framework_hint(
            repository
        )

        evidence: list[str] = []

        if manage_py_found:
            evidence.append(
                "Django manage.py project marker detected."
            )

        if _has_django_marker(
            python_text
        ):
            evidence.append(
                "Django configuration/dependency evidence detected."
            )

        if drf_dependency_found:
            evidence.append(
                "Django REST Framework dependency detected."
            )

        if drf_import_found:
            evidence.append(
                "Django REST Framework import evidence detected."
            )

        if (
            hint == self.adapter_type
            and not evidence
        ):
            evidence.append(
                "Repository scanner explicitly identified Django REST Framework."
            )

        # Strong detection:
        # Django project + DRF dependency/import.
        if django_found and (
            drf_dependency_found
            or drf_import_found
        ):
            return AdapterDetectionResult(
                detected=True,
                adapter_type=self.adapter_type,
                framework_name=self.framework_name,
                language=self.language,
                confidence="high",
                evidence=tuple(
                    evidence
                ),
                reason=(
                    "Django and Django REST Framework evidence "
                    "were detected."
                ),
            )

        return AdapterDetectionResult(
            detected=False,
            adapter_type=self.adapter_type,
            framework_name=self.framework_name,
            language=self.language,
            confidence="none",
            evidence=tuple(
                evidence
            ),
            reason=(
                "Django REST Framework could not be confirmed "
                "from repository evidence."
            ),
        )

    # ------------------------------------------------------------------
    # Generation capability
    # ------------------------------------------------------------------

    def can_generate_contract(
        self,
        repository: Mapping[str, Any],
    ) -> bool:
        """Return whether deterministic CI generation can be prepared."""

        detection = self.detect(repository)
        if not detection.detected:
            return False

        paths = _paths_from_repository(repository)
        manage_py = _find_manage_py(paths)
        if not manage_py:
            return False

        # The centralized action currently knows how to bootstrap pip-based
        # Python repositories. Require a supported dependency manifest when
        # automated generation is requested so we do not create a workflow
        # that will deterministically fail at dependency installation time.
        working_directory = _path_directory(manage_py)
        return bool(
            _dependency_files_for_repository(
                repository,
                working_directory=working_directory,
            )
        )

    # ------------------------------------------------------------------
    # Contract generation plan
    # ------------------------------------------------------------------

    def generate_contract(
        self,
        repository: Mapping[str, Any],
        *,
        commit_sha: str,
    ) -> ContractGenerationPlan:
        """Create the complete runtime-aware DRF contract generation plan."""

        if not isinstance(repository, Mapping):
            raise ValueError("Repository metadata is invalid.")

        validated_sha = _validate_commit_sha(commit_sha)
        detection = self.detect(repository)

        if not detection.detected:
            return ContractGenerationPlan(
                supported=False,
                adapter_type=self.adapter_type,
                language=self.language,
                package_manager=PACKAGE_MANAGER,
                adapter_name=self.adapter_type,
                adapter_version=ADAPTER_VERSION,
                reason=(
                    "Django REST Framework was not detected. "
                    "Contract generation cannot be prepared."
                ),
            )

        paths = _paths_from_repository(repository)
        manage_py = _find_manage_py(paths)
        if not manage_py:
            return ContractGenerationPlan(
                supported=False,
                adapter_type=self.adapter_type,
                language=self.language,
                package_manager=PACKAGE_MANAGER,
                adapter_name=self.adapter_type,
                adapter_version=ADAPTER_VERSION,
                warnings=(
                    "manage.py was not found in the scanned repository tree.",
                ),
                reason=(
                    "Django REST Framework was detected, but automated contract "
                    "generation requires a repository-local manage.py entry point."
                ),
            )

        working_directory = _path_directory(manage_py)
        dependency_files = _dependency_files_for_repository(
            repository,
            working_directory=working_directory,
        )

        if not dependency_files:
            return ContractGenerationPlan(
                supported=False,
                adapter_type=self.adapter_type,
                language=self.language,
                package_manager=PACKAGE_MANAGER,
                working_directory=working_directory,
                adapter_name=self.adapter_type,
                adapter_version=ADAPTER_VERSION,
                warnings=(
                    "No supported Python dependency manifest was found.",
                ),
                reason=(
                    "Django REST Framework was detected, but the centralized "
                    "CI runtime currently requires a supported pip dependency "
                    "manifest for automatic setup."
                ),
            )

        warnings: list[str] = []
        spectacular_present = _drf_spectacular_present(repository)
        if not spectacular_present:
            warnings.append(
                "drf-spectacular was not found in the scanned dependency manifests. "
                "The CI runtime plan will install it before contract generation."
            )

        # The contract is generated at repository root so the setup service can
        # use the same deterministic default spec path for generated contracts.
        # The command executes from working_directory (the directory that
        # contains manage.py), so the invocation must be relative to that
        # directory. Paths are shell-quoted because repository paths are
        # untrusted input and the centralized action executes this command.
        manage_command = shlex.quote(
            PurePosixPath(manage_py).name
        )
        output_path = (
            posixpath.join(working_directory, DEFAULT_OUTPUT_PATH)
            if working_directory
            else DEFAULT_OUTPUT_PATH
        )
        relative_output_path = (
            posixpath.relpath(output_path, working_directory or ".")
        )
        command = (
            f"python {manage_command} spectacular "
            f"--file {shlex.quote(relative_output_path)} --validate"
        )

        if dependency_files:
            install_command = _install_command_for_dependency(
                dependency_files[0],
                working_directory=working_directory,
                install_drf_spectacular=not spectacular_present,
            )
        else:
            # Keep the plan explicit even though can_generate_contract() will
            # normally reject repositories without a supported manifest.
            install_command = (
                "python -m pip install drf-spectacular"
                if not spectacular_present
                else ""
            )

        warnings.append(
            f"Contract must be generated from exact commit {validated_sha}."
        )

        return ContractGenerationPlan(
            supported=True,
            adapter_type=self.adapter_type,
            language=self.language,
            package_manager=PACKAGE_MANAGER,
            dependency_files=dependency_files,
            command=command,
            output_path=output_path,
            working_directory=working_directory,
            install_command=install_command,
            environment={},
            adapter_name=self.adapter_type,
            adapter_version=ADAPTER_VERSION,
            warnings=tuple(warnings),
            reason=(
                "Generate an OpenAPI contract with drf-spectacular from the "
                "exact repository revision using a pip-based CI runtime plan."
            ),
        )

    # ------------------------------------------------------------------
    # Contract validation
    # ------------------------------------------------------------------

    def validate_contract(
        self,
        contract: Mapping[str, Any],
    ) -> ContractValidationResult:
        """
        Perform deterministic structural validation of an OpenAPI/Swagger
        contract.

        This is intentionally dependency-light. A later shared validation
        layer can perform full OpenAPI specification validation.

        This adapter ensures that an apparently successful generation has the
        minimum structure expected by the analyzer.
        """

        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(
            contract,
            Mapping,
        ):
            return ContractValidationResult(
                valid=False,
                errors=(
                    "Contract must be a JSON object.",
                ),
            )

        openapi_version = str(
            contract.get("openapi")
            or ""
        ).strip()

        swagger_version = str(
            contract.get("swagger")
            or ""
        ).strip()

        contract_type = ""

        if openapi_version:
            contract_type = "openapi"

            if not any(
                openapi_version.startswith(prefix)
                for prefix in SUPPORTED_OPENAPI_PREFIXES
            ):
                errors.append(
                    f"Unsupported OpenAPI version '{openapi_version}'."
                )

        elif swagger_version:
            contract_type = "swagger"

            if swagger_version != SUPPORTED_SWAGGER_VERSION:
                errors.append(
                    f"Unsupported Swagger version '{swagger_version}'."
                )

        else:
            errors.append(
                "Contract does not declare an OpenAPI or Swagger version."
            )

        info = contract.get(
            "info"
        )

        if not isinstance(
            info,
            Mapping,
        ):
            errors.append(
                "Contract must contain an 'info' object."
            )
        else:
            title = str(
                info.get("title")
                or ""
            ).strip()

            version = str(
                info.get("version")
                or ""
            ).strip()

            if not title:
                warnings.append(
                    "Contract info.title is missing."
                )

            if not version:
                warnings.append(
                    "Contract info.version is missing."
                )

        paths = contract.get(
            "paths"
        )

        if not isinstance(
            paths,
            Mapping,
        ):
            errors.append(
                "Contract must contain a 'paths' object."
            )

        elif not paths:
            warnings.append(
                "Contract contains no API paths."
            )

        components = contract.get(
            "components"
        )

        if (
            components is not None
            and not isinstance(
                components,
                Mapping,
            )
        ):
            errors.append(
                "Contract 'components' must be an object when present."
            )

        if errors:
            return ContractValidationResult(
                valid=False,
                contract_type=contract_type,
                openapi_version=(
                    openapi_version
                    or swagger_version
                ),
                errors=tuple(
                    errors
                ),
                warnings=tuple(
                    warnings
                ),
            )

        return ContractValidationResult(
            valid=True,
            contract_type=contract_type,
            openapi_version=(
                openapi_version
                or swagger_version
            ),
            errors=(),
            warnings=tuple(
                warnings
            ),
        )

    # ------------------------------------------------------------------
    # Contract source description
    # ------------------------------------------------------------------

    def describe_source(
        self,
        repository: Mapping[str, Any],
    ) -> ContractSourceDescription:
        """
        Describe the contract source that this adapter will use.
        """

        if not isinstance(
            repository,
            Mapping,
        ):
            return ContractSourceDescription(
                source_type="generated",
                name="Django REST Framework",
                description=(
                    "OpenAPI contract generated from a Django REST Framework "
                    "application during CI."
                ),
                path=DEFAULT_OUTPUT_PATH,
                generated=True,
            )

        existing_path = _existing_contract_path(
            repository
        )

        if existing_path:
            contract = _contract_from_repository(
                repository
            )

            source_type = str(
                contract.get("source")
                or contract.get("source_type")
                or "committed"
            ).strip()

            return ContractSourceDescription(
                source_type=source_type,
                name="Repository OpenAPI contract",
                description=(
                    "Use the repository's existing OpenAPI/Swagger contract "
                    "as the contract source."
                ),
                path=existing_path,
                generated=False,
            )

        return ContractSourceDescription(
            source_type="generated",
            name="Django REST Framework generated OpenAPI",
            description=(
                "Generate the OpenAPI contract during CI using "
                "drf-spectacular."
            ),
            path=DEFAULT_OUTPUT_PATH,
            generated=True,
        )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def metadata(self) -> dict[str, Any]:
        """
        Return safe adapter metadata.

        No repository contents, credentials or secrets are returned.
        """

        return {
            "adapter_type": self.adapter_type,
            "adapter_name": self.adapter_type,
            "adapter_version": ADAPTER_VERSION,
            "framework_name": self.framework_name,
            "language": self.language,
            "package_manager": PACKAGE_MANAGER,
            "contract_generator": "drf-spectacular",
            "default_contract_format": "OpenAPI",
            "default_output_path": DEFAULT_OUTPUT_PATH,
        }


__all__ = [
    "DjangoRESTFrameworkAdapter",
]
