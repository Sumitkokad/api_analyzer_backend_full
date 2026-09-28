"""
API Analyzer contract adapters.

Adapters isolate framework-specific contract acquisition and validation
from the framework-agnostic API compatibility engine.
"""

from .base import ContractAdapter
from .registry import AdapterRegistry

__all__ = [
    "ContractAdapter",
    "AdapterRegistry",
]