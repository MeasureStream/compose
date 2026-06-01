from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional


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
