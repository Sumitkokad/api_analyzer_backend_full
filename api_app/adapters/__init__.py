"""
API Analyzer contract adapters.

Adapters isolate framework-specific contract acquisition and validation
from the framework-agnostic API compatibility engine.
"""

from .base import (
    AdapterDetectionResult,
    ContractAdapter,
    ContractGenerationPlan,
    ContractSourceDescription,
    ContractValidationResult,
)
from .drf import DjangoRESTFrameworkAdapter
from .registry import AdapterRegistry, AdapterResolution

__all__ = [
    "AdapterDetectionResult",
    "ContractAdapter",
    "ContractGenerationPlan",
    "ContractSourceDescription",
    "ContractValidationResult",
    "DjangoRESTFrameworkAdapter",
    "AdapterRegistry",
    "AdapterResolution",
]
