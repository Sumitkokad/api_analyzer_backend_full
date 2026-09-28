"""
Contract source implementations.

Contract sources describe how an API contract is obtained for a specific
repository revision. The compatibility engine remains framework-agnostic.
"""

from .base import ContractSource, ContractSourceResult

__all__ = [
    "ContractSource",
    "ContractSourceResult",
]