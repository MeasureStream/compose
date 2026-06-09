from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


def _lookup(lst, key, val, default=None):
    for item in lst:
        if isinstance(item, dict) and item.get(key) == val:
            return item
    return default


class SensorAccuracyChecker:
    """Checks as-found errors against declared sensorAccuracy ranges."""

    def __init__(self, accuracy_ranges: List[Dict[str, Any]]):
        self.accuracy_ranges = accuracy_ranges

    def max_error_at_temperature(self, temp_y: float) -> float:
        applicable = [
            r["maxError"]
            for r in self.accuracy_ranges
            if r["tempMin"] <= temp_y <= r["tempMax"]
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
                "T_ref_y": t_ref,
                "as_found_error_y": err,
                "max_allowed_error_y": max_err,
                "in_range": in_range,
            })
        return {"all_in_range": all_in_range, "per_point": per_point}


def lsb_to_y(lsb: float, lsb_scale: Dict[str, Any], adc_max: float) -> float:
    """Convert raw ADC count [LSB] to physical unit [Y] using the LSB scale."""
    min_v = float(lsb_scale.get("minPhysVal", -40.0))
    max_v = float(lsb_scale.get("maxPhysVal", 125.0))
    return min_v + (lsb / adc_max) * (max_v - min_v)


def y_to_lsb(y: float, lsb_scale: Dict[str, Any], adc_max: float) -> float:
    """Convert physical value [Y] to ADC count [LSB] using the LSB scale."""
    min_v = float(lsb_scale.get("minPhysVal", -40.0))
    max_v = float(lsb_scale.get("maxPhysVal", 125.0))
    span  = max(max_v - min_v, 1e-12)
    return (y - min_v) / span * adc_max


def round_to_significant_figures(value: float, sig: int = 2) -> float:
    if value == 0.0:
        return 0.0
    return round(value, sig - 1 - int(math.floor(math.log10(abs(value)))))


def parse_uncertainty_limit(limit_str: str) -> Optional[float]:
    m = re.search(r"([\d.]+)", limit_str)
    return float(m.group(1)) if m else None


# ── Deprecated legacy aliases (only for backward compat with old callers) ────
# Use y_to_lsb / lsb_to_y directly.  These aliases will be removed in a future
# major version.
degc_to_lsb = y_to_lsb   # deprecated — use y_to_lsb
lsb_to_degc = lsb_to_y   # deprecated — use lsb_to_y

