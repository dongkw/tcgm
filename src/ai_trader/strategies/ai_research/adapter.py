"""Compatibility imports for the provider-neutral constrained AI interface."""

from ...ai_interface.contracts import AIProviderUnavailable as AIAdapterUnavailable
from ...ai_interface.providers import (
    AIProvider as AIAdapter,
    CodexProvider as UnavailableAIAdapter,
    ManualProvider as FakeAIAdapter,
)

__all__ = ["AIAdapter", "AIAdapterUnavailable", "FakeAIAdapter", "UnavailableAIAdapter"]
