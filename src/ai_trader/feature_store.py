"""Read-only access to public features in an analysis snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .strategy_platform.contracts import AnalysisSnapshot


MISSING = object()


@dataclass(frozen=True)
class FeatureDependencyResult:
    available: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing


class FeatureStore:
    """Resolve dotted feature paths without exposing mutable state."""

    def __init__(self, snapshot: AnalysisSnapshot):
        self._features = snapshot.features

    def get(self, path: str, default: Any = None) -> Any:
        current: Any = self._features
        for part in path.split("."):
            if not isinstance(current, Mapping) or part not in current:
                return default
            current = current[part]
        return current

    def contains(self, path: str) -> bool:
        return self.get(path, MISSING) is not MISSING

    def check(self, paths: tuple[str, ...]) -> FeatureDependencyResult:
        available: list[str] = []
        missing: list[str] = []
        for path in paths:
            value = self.get(path, MISSING)
            if value is MISSING or value is None or value == "":
                missing.append(path)
            else:
                available.append(path)
        return FeatureDependencyResult(tuple(available), tuple(missing))
