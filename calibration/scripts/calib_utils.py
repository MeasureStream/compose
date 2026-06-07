from __future__ import annotations

import math
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional


class JsonView:
    """Thin dict-like wrapper around JSON data.

    It keeps normal dict/list behavior but also lets you access list-wrapped
    singleton objects like dictionaries, which is common in metrology payloads.
    """

    def __init__(self, value: Any):
        self._value = value

    @classmethod
    def from_path(cls, path: str | Path) -> "JsonView":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    @staticmethod
    def _wrap(value: Any) -> Any:
        if isinstance(value, (dict, list)):
            return JsonView(value)
        return value

    def unwrap(self) -> Any:
        if isinstance(self._value, dict):
            return {key: JsonView._unwrap(item) for key, item in self._value.items()}
        if isinstance(self._value, list):
            return [JsonView._unwrap(item) for item in self._value]
        return self._value

    @staticmethod
    def _unwrap(value: Any) -> Any:
        return value.unwrap() if isinstance(value, JsonView) else value

    def get(self, key: Any, default: Any = None) -> Any:
        if isinstance(self._value, dict):
            return self._wrap(self._value.get(key, default))
        if isinstance(self._value, list) and len(self._value) == 1:
            return JsonView(self._value[0]).get(key, default)
        return default

    def find(self, key: str, value: Any, default: Any = None) -> Any:
        if isinstance(self._value, list):
            for item in self._value:
                if isinstance(item, dict) and item.get(key) == value:
                    return JsonView(item)
        return default

    def one(self, default: Any = None) -> Any:
        if isinstance(self._value, list) and len(self._value) == 1:
            return JsonView(self._value[0])
        return default

    def __getitem__(self, key: Any) -> Any:
        if isinstance(self._value, dict):
            return self._wrap(self._value[key])
        if isinstance(self._value, list):
            if isinstance(key, int):
                return self._wrap(self._value[key])
            if len(self._value) == 1:
                return JsonView(self._value[0])[key]
        raise TypeError(f"{type(self._value).__name__} does not support key access with {key!r}")

    def __iter__(self):
        if isinstance(self._value, dict):
            return iter(self._value)
        if isinstance(self._value, list):
            return (self._wrap(item) for item in self._value)
        raise TypeError(f"{type(self._value).__name__} is not iterable")

    def __len__(self) -> int:
        if isinstance(self._value, (dict, list)):
            return len(self._value)
        raise TypeError(f"len() is not supported for {type(self._value).__name__}")

    def __contains__(self, item: Any) -> bool:
        if isinstance(self._value, (dict, list)):
            return item in self._value
        return False

    def items(self):
        if isinstance(self._value, dict):
            return self._value.items()
        raise TypeError(f"{type(self._value).__name__} has no items()")

    def keys(self):
        if isinstance(self._value, dict):
            return self._value.keys()
        raise TypeError(f"{type(self._value).__name__} has no keys()")

    def values(self):
        if isinstance(self._value, dict):
            return (self._wrap(item) for item in self._value.values())
        raise TypeError(f"{type(self._value).__name__} has no values()")

    def __repr__(self) -> str:
        return f"JsonView({self._value!r})"


class SensorAccuracyChecker:
    """Checks as-found errors against declared sensorAccuracy ranges."""

    def __init__(self, accuracy_ranges: List[Dict[str, Any]]):
        self.accuracy_ranges = accuracy_ranges

    def max_error_at_temperature(self, temp_degc: float) -> float:
        applicable = [
            r["maxError"]
            for r in self.accuracy_ranges
            if r["tempMin"] <= temp_degc <= r["tempMax"]
        ]
        return min(applicable) if applicable else float("inf")

    def check_all_points(
        self,
        ref_temp_means: List[float],
        as_found_errors: List[float],
    ) -> Dict[str, Any]:
        per_point = []
        all_in_range = True
        for i, (t_ref, err) in enumerate(zip(ref_temp_means, as_found_errors)):
            max_err = self.max_error_at_temperature(t_ref)
            in_range = abs(err) <= max_err
            if not in_range:
                all_in_range = False
            per_point.append({
                "point": i + 1,
                "T_ref_degC": t_ref,
                "as_found_error_degC": err,
                "max_allowed_error_degC": max_err,
                "in_range": in_range,
            })
        return {"all_in_range": all_in_range, "per_point": per_point}


def lsb_to_degc(lsb: float, lsb_scale: Dict[str, Any], adc_max: float) -> float:
    min_v = float(lsb_scale.get("minPhysVal", -40.0))
    max_v = float(lsb_scale.get("maxPhysVal", 125.0))
    return min_v + (lsb / adc_max) * (max_v - min_v)


def round_to_significant_figures(value: float, sig: int = 2) -> float:
    if value == 0.0:
        return 0.0
    return round(value, sig - 1 - int(math.floor(math.log10(abs(value)))))


def parse_uncertainty_limit(limit_str: str) -> Optional[float]:
    m = re.search(r"([\d.]+)", limit_str)
    return float(m.group(1)) if m else None
