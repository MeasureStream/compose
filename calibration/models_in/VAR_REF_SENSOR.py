from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class SENSOR_model:
    _data: Dict[str, Any] = field(default_factory=dict)

    templateId: int = 201
    mpn: Optional[str] = "NTCLP450E3"
    manufacturer: Optional[str] = "Vishay"
    datasheet: Optional[str] = "https://www.vishay.com/docs/29110/ntclp450e3.pdf"
    type: str = "temperature"
    unit: str = "\\degreeCelsius"

    minPhysVal: float = -40.0
    maxPhysVal: float = 105.0
    _minPhyThreshold: float = -40.0
    _maxPhyThreshold: float = 105.0
    PhySI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    PhySIExp: int = 0
    PhyDSI: str = "\\degreeCelsius"

    minPhyROC: float = 0.0
    maxPhyROC: float = 105.0
    PhyROCSIExp: int = 0
    PhyROCSI: List[int] = field(default_factory=lambda: [0, 0, -1, 0, 1, 0, 0, 0])
    PhyROCDSI: str = "\\degreeCelsius\\per\\second"

    minElecVal: float = 0.0
    maxElecVal: float = 65535.0
    ElecSI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 0, 0, 0, 0])
    ElecSIExp: int = 0
    ElecDSI: str = "\\one"

    maxSamplingT: float = 0.0
    minSamplingT: float = 0.001
    SamplingTSI: List[int] = field(default_factory=lambda: [0, 0, 1, 0, 0, 0, 0, 0])
    SamplingTSIExp: int = 0
    SamplingTDSI: str = "\\second"

    ReadingConsumption: float = 0.5
    ReadingConsumptionSIExp: int = -9
    ReadingConsumptionSI: List[int] = field(default_factory=lambda: [0, 0, 1, 1, 0, 0, 0, 0])
    ReadingConsumptionDSI: str = "\\coulomb"

    ResponseTime: float = 0.16
    ResponseTimeSIExp: int = 0
    ResponseTimeSI: List[int] = field(default_factory=lambda: [0, 0, 1, 0, -1, 0, 0, 0])
    ResponseTimeDSI: str = "\\second\\per\\degreeCelsius"

    selfHeat: float = 0.0
    SelfHeatSIExp: int = 0
    SelfHeatSI: List[int] = field(default_factory=lambda: [0, 0, -1, 0, 1, 0, 0, 0])
    SelfHeatDSI: str = "\\kelvin\\per\\second"

    absUncertainty: float = 5.0
    UncertaintyPDF: str = "uniform"
    uB: float = 2.9
    K: float = 2.0

    calibrationProcedure: str = "qubic-interpolation"
    coeffA: float = 0.0
    coeffASIExp: int = 0
    coeffASI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    coeffADSI: str = "\\degreeCelsius"

    coeffB: float = 0.0
    coeffBSIExp: int = 0
    coeffBSI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    coeffBDSI: str = "\\degreeCelsius"

    coeffC: float = 0.0
    coeffCSIExp: int = 0
    coeffCSI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    coeffCDSI: str = "\\degreeCelsius"

    coeffD: float = 0.0
    coeffDSIExp: int = 0
    coeffDSI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    coeffDDSI: str = "\\degreeCelsius"

    R25: float = 10000.0
    B25_85: float = 3950.0
    A_steinhart: float = 0.001129148
    B_steinhart: float = 0.000234125
    C_steinhart: float = 8.76741e-8
    alpha_25: float = -0.044430

    uncertainty_limit: str = "within 0.10 C"
    interpolation_uncertainty_fixed_degC: float = 0.05

    obs_list: List[str] = field(default_factory=lambda: [
        "Results refer exclusively to the instrument identified in this certificate.",
        "Calibration is valid under the declared conditions and on the execution date.",
        "Reference temperature T_ref measured by PT100/Fluke 1502A (mean per step).",
        "Sensor temperature T_sensor measured by NTC via TMP126 ADC (mean per step).",
    ])

    method_description: str = (
        "GUM-compliant Ordinary Least Squares (OLS) linear regression in the "
        "16-bit LSB domain. The calibration model is T_ref_lsb = A * T_sensor_lsb + B, "
        "where T_ref_lsb are the PT100 reference readings (converted to LSB) and "
        "T_sensor_lsb are the raw NTC ADC readings. Uncertainty propagation follows "
        "ISO/IEC Guide 98-3 (GUM)."
    )

    calibration_formula: str = "T = A * dout + B"
    formula_steinhart: str = "1/T = A_sh + B_sh*ln(R) + C_sh*(ln R)^3"
    formula_beta: str = "R(T) = R25 * exp[B25_85 * (1/T - 1/T25)]"

    certificate_variant: str = "centigradi"
    resolution_degC: float = 0.01
    settling_period_min: int = 10

    def __post_init__(self):
        json_path = BASE_DIR / "ntc_temperature.json"
        if json_path.exists():
            self._data = json.loads(json_path.read_text(encoding="utf-8"))
            self._load_from_json(self._data)

    @classmethod
    def from_json(cls, path: Path) -> "SENSOR_model":
        """Load a SENSOR_model from an explicit JSON path (bypasses BASE_DIR default)."""
        obj = cls.__new__(cls)
        # initialise dataclass fields with their defaults without triggering __post_init__
        obj._data = {}
        obj.templateId = 201
        obj.mpn = "NTCLP450E3"
        obj.manufacturer = "Vishay"
        obj.datasheet = "https://www.vishay.com/docs/29110/ntclp450e3.pdf"
        obj.type = "temperature"
        obj.unit = "\\degreeCelsius"
        obj.minPhysVal = -40.0
        obj.maxPhysVal = 105.0
        obj._minPhyThreshold = -40.0
        obj._maxPhyThreshold = 105.0
        obj.PhySI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.PhySIExp = 0
        obj.PhyDSI = "\\degreeCelsius"
        obj.minPhyROC = 0.0
        obj.maxPhyROC = 105.0
        obj.PhyROCSIExp = 0
        obj.PhyROCSI = [0, 0, -1, 0, 1, 0, 0, 0]
        obj.PhyROCDSI = "\\degreeCelsius\\per\\second"
        obj.minElecVal = 0.0
        obj.maxElecVal = 65535.0
        obj.ElecSI = [0, 0, 0, 0, 0, 0, 0, 0]
        obj.ElecSIExp = 0
        obj.ElecDSI = "\\one"
        obj.maxSamplingT = 0.0
        obj.minSamplingT = 0.001
        obj.SamplingTSI = [0, 0, 1, 0, 0, 0, 0, 0]
        obj.SamplingTSIExp = 0
        obj.SamplingTDSI = "\\second"
        obj.ReadingConsumption = 0.5
        obj.ReadingConsumptionSIExp = -9
        obj.ReadingConsumptionSI = [0, 0, 1, 1, 0, 0, 0, 0]
        obj.ReadingConsumptionDSI = "\\coulomb"
        obj.ResponseTime = 0.16
        obj.ResponseTimeSIExp = 0
        obj.ResponseTimeSI = [0, 0, 1, 0, -1, 0, 0, 0]
        obj.ResponseTimeDSI = "\\second\\per\\degreeCelsius"
        obj.selfHeat = 0.0
        obj.SelfHeatSIExp = 0
        obj.SelfHeatSI = [0, 0, -1, 0, 1, 0, 0, 0]
        obj.SelfHeatDSI = "\\kelvin\\per\\second"
        obj.absUncertainty = 5.0
        obj.UncertaintyPDF = "uniform"
        obj.uB = 2.9
        obj.K = 2.0
        obj.calibrationProcedure = "qubic-interpolation"
        obj.coeffA = 0.0
        obj.coeffASIExp = 0
        obj.coeffASI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.coeffADSI = "\\degreeCelsius"
        obj.coeffB = 0.0
        obj.coeffBSIExp = 0
        obj.coeffBSI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.coeffBDSI = "\\degreeCelsius"
        obj.coeffC = 0.0
        obj.coeffCSIExp = 0
        obj.coeffCSI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.coeffCDSI = "\\degreeCelsius"
        obj.coeffD = 0.0
        obj.coeffDSIExp = 0
        obj.coeffDSI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.coeffDDSI = "\\degreeCelsius"
        obj.R25 = 10000.0
        obj.B25_85 = 3950.0
        obj.A_steinhart = 0.001129148
        obj.B_steinhart = 0.000234125
        obj.C_steinhart = 8.76741e-8
        obj.alpha_25 = -0.044430
        obj.uncertainty_limit = "within 0.10 C"
        obj.interpolation_uncertainty_fixed_degC = 0.05
        obj.obs_list = [
            "Results refer exclusively to the instrument identified in this certificate.",
            "Calibration is valid under the declared conditions and on the execution date.",
            "Reference temperature T_ref measured by PT100/Fluke 1502A (mean per step).",
            "Sensor temperature T_sensor measured by NTC via TMP126 ADC (mean per step).",
        ]
        obj.method_description = (
            "GUM-compliant Ordinary Least Squares (OLS) linear regression in the "
            "16-bit LSB domain. The calibration model is T_ref_lsb = A * T_sensor_lsb + B, "
            "where T_ref_lsb are the PT100 reference readings (converted to LSB) and "
            "T_sensor_lsb are the raw NTC ADC readings. Uncertainty propagation follows "
            "ISO/IEC Guide 98-3 (GUM)."
        )
        obj.calibration_formula = "T = A * dout + B"
        obj.formula_steinhart = "1/T = A_sh + B_sh*ln(R) + C_sh*(ln R)^3"
        obj.formula_beta = "R(T) = R25 * exp[B25_85 * (1/T - 1/T25)]"
        obj.certificate_variant = "centigradi"
        obj.resolution_degC = 0.01
        obj.settling_period_min = 10

        path = Path(path)
        if path.exists():
            obj._data = json.loads(path.read_text(encoding="utf-8"))
            obj._load_from_json(obj._data)
        return obj

    def _load_from_json(self, data: Dict[str, Any]):
        self.templateId = data.get("templateId", self.templateId)
        self.mpn = data.get("mpn", self.mpn)
        self.manufacturer = data.get("manufacturer", self.manufacturer)
        self.datasheet = data.get("datasheet", self.datasheet)
        self.type = data.get("type", self.type)
        self.unit = data.get("unit", self.unit)

        ranges = data.get("ranges", {})
        phys = ranges.get("phys", {})
        self.minPhysVal = phys.get("min", self.minPhysVal)
        self.maxPhysVal = phys.get("max", self.maxPhysVal)
        self.PhySI = phys.get("si", self.PhySI)
        self.PhySIExp = phys.get("siExp", self.PhySIExp)
        self.PhyDSI = phys.get("dsi", self.PhyDSI)

        phys_roc = ranges.get("physROC", {})
        self.minPhyROC = phys_roc.get("min", self.minPhyROC)
        self.maxPhyROC = phys_roc.get("max", self.maxPhyROC)
        self.PhyROCSI = phys_roc.get("si", self.PhyROCSI)
        self.PhyROCSIExp = phys_roc.get("siExp", self.PhyROCSIExp)
        self.PhyROCDSI = phys_roc.get("dsi", self.PhyROCDSI)

        elec = ranges.get("elec", {})
        self.minElecVal = elec.get("min", self.minElecVal)
        self.maxElecVal = elec.get("max", self.maxElecVal)
        self.ElecSI = elec.get("si", self.ElecSI)
        self.ElecSIExp = elec.get("siExp", self.ElecSIExp)
        self.ElecDSI = elec.get("dsi", self.ElecDSI)

        sampling = ranges.get("sampling", {})
        self.minSamplingT = sampling.get("min", self.minSamplingT)
        self.maxSamplingT = sampling.get("max", self.maxSamplingT)
        self.SamplingTSI = sampling.get("si", self.SamplingTSI)
        self.SamplingTSIExp = sampling.get("siExp", self.SamplingTSIExp)
        self.SamplingTDSI = sampling.get("dsi", self.SamplingTDSI)

        threshold = ranges.get("threshold", {})
        if "min" in threshold:
            self._minPhyThreshold = float(threshold["min"])
        if "max" in threshold:
            self._maxPhyThreshold = float(threshold["max"])

        properties = data.get("properties", {})
        self.ResponseTime = properties.get("responseTime", self.ResponseTime)
        self.ResponseTimeSI = properties.get("responseTimeSi", self.ResponseTimeSI)
        self.ResponseTimeSIExp = properties.get("responseTimeSiExp", self.ResponseTimeSIExp)
        self.ResponseTimeDSI = properties.get("responseTimeDsi", self.ResponseTimeDSI)
        self.selfHeat = properties.get("selfHeat", self.selfHeat)
        self.SelfHeatSI = properties.get("selfHeatSi", self.SelfHeatSI)
        self.SelfHeatSIExp = properties.get("selfHeatSiExp", self.SelfHeatSIExp)
        self.SelfHeatDSI = properties.get("selfHeatDsi", self.SelfHeatDSI)
        self.ReadingConsumption = properties.get("readingConsumption", self.ReadingConsumption)
        self.ReadingConsumptionSI = properties.get("readingConsumptionSi", self.ReadingConsumptionSI)
        self.ReadingConsumptionSIExp = properties.get("readingConsumptionSiExp", self.ReadingConsumptionSIExp)
        self.ReadingConsumptionDSI = properties.get("readingConsumptionDsi", self.ReadingConsumptionDSI)

        calibration = data.get("calibration", {})
        self.calibrationProcedure = calibration.get("type", self.calibrationProcedure)

        coeffs = calibration.get("calibrationCoefficients", {})
        if "A" in coeffs:
            self.coeffA = coeffs["A"].get("value", self.coeffA) if isinstance(coeffs["A"], dict) else coeffs["A"]
        if "B" in coeffs:
            self.coeffB = coeffs["B"].get("value", self.coeffB) if isinstance(coeffs["B"], dict) else coeffs["B"]
        if "C" in coeffs:
            self.coeffC = coeffs["C"].get("value", self.coeffC) if isinstance(coeffs["C"], dict) else coeffs["C"]
        if "D" in coeffs:
            self.coeffD = coeffs["D"].get("value", self.coeffD) if isinstance(coeffs["D"], dict) else coeffs["D"]

        metrology = data.get("metrology", {})
        self.UncertaintyPDF = metrology.get("UncertaintyPdf", self.UncertaintyPDF)

        reading_unc = metrology.get("readingUncertainty", [])
        for item in reading_unc:
            var_name = item.get("varName", "")
            if var_name == "absUncertainty":
                self.absUncertainty = item.get("value", self.absUncertainty)
            elif var_name == "uB":
                self.uB = item.get("value", self.uB)
            elif var_name == "coverageFactor":
                self.K = item.get("value", self.K)


@dataclass
class RIFERIMENTO_model:
    _data: Dict[str, Any] = field(default_factory=dict)

    templateId: int = 301
    mpn: Optional[str] = "9142"
    manufacturer: Optional[str] = "Fluke / Hart Scientific"
    datasheet: Optional[str] = "fluke_well_9142.pdf"
    type: str = "temperature_calibrator"
    unit: str = "\\degreeCelsius"

    minPhysVal: float = -25.0
    maxPhysVal: float = 150.0
    PhySI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    PhySIExp: int = 0
    PhyDSI: str = "\\degreeCelsius"

    minOperatingEnv: float = 0.0
    maxOperatingEnv: float = 50.0
    OperatingEnvSI: List[int] = field(default_factory=lambda: [0, 0, 0, 0, 1, 0, 0, 0])
    OperatingEnvSIExp: int = 0
    OperatingEnvDSI: str = "\\degreeCelsius"

    immersionDepth: float = 150.0
    immersionDepthSiExp: int = -3
    immersionDepthSi: List[int] = field(default_factory=lambda: [0, 1, 0, 0, 0, 0, 0, 0])
    immersionDepthDsi: str = "\\meter"

    heatingTimeMax: float = 1500.0
    heatingTimeSiExp: int = 0
    heatingTimeSi: List[int] = field(default_factory=lambda: [0, 0, 1, 0, 0, 0, 0, 0])
    heatingTimeDsi: str = "\\second"

    coolingTimeMax: float = 900.0
    coolingTimeSiExp: int = 0
    coolingTimeSi: List[int] = field(default_factory=lambda: [0, 0, 1, 0, 0, 0, 0, 0])
    coolingTimeDsi: str = "\\second"

    evaluationFormula: str = "RSS"
    UncertaintyPdf: str = "gaussian"

    def __post_init__(self):
        json_path = BASE_DIR / "fluke_9142.json"
        if json_path.exists():
            self._data = json.loads(json_path.read_text(encoding="utf-8"))
            self._load_from_json(self._data)

    @classmethod
    def from_json(cls, path: Path) -> "RIFERIMENTO_model":
        """Load a RIFERIMENTO_model from an explicit JSON path (bypasses BASE_DIR default)."""
        obj = cls.__new__(cls)
        obj._data = {}
        obj.templateId = 301
        obj.mpn = "9142"
        obj.manufacturer = "Fluke / Hart Scientific"
        obj.datasheet = "fluke_well_9142.pdf"
        obj.type = "temperature_calibrator"
        obj.unit = "\\degreeCelsius"
        obj.minPhysVal = -25.0
        obj.maxPhysVal = 150.0
        obj.PhySI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.PhySIExp = 0
        obj.PhyDSI = "\\degreeCelsius"
        obj.minOperatingEnv = 0.0
        obj.maxOperatingEnv = 50.0
        obj.OperatingEnvSI = [0, 0, 0, 0, 1, 0, 0, 0]
        obj.OperatingEnvSIExp = 0
        obj.OperatingEnvDSI = "\\degreeCelsius"
        obj.immersionDepth = 150.0
        obj.immersionDepthSiExp = -3
        obj.immersionDepthSi = [0, 1, 0, 0, 0, 0, 0, 0]
        obj.immersionDepthDsi = "\\meter"
        obj.heatingTimeMax = 1500.0
        obj.heatingTimeSiExp = 0
        obj.heatingTimeSi = [0, 0, 1, 0, 0, 0, 0, 0]
        obj.heatingTimeDsi = "\\second"
        obj.coolingTimeMax = 900.0
        obj.coolingTimeSiExp = 0
        obj.coolingTimeSi = [0, 0, 1, 0, 0, 0, 0, 0]
        obj.coolingTimeDsi = "\\second"
        obj.evaluationFormula = "RSS"
        obj.UncertaintyPdf = "gaussian"

        path = Path(path)
        if path.exists():
            obj._data = json.loads(path.read_text(encoding="utf-8"))
            obj._load_from_json(obj._data)
        return obj

    def _load_from_json(self, data: Dict[str, Any]):
        self.templateId = data.get("templateId", self.templateId)
        self.mpn = data.get("mpn", self.mpn)
        self.manufacturer = data.get("manufacturer", self.manufacturer)
        self.datasheet = data.get("datasheet", self.datasheet)
        self.type = data.get("type", self.type)
        self.unit = data.get("unit", self.unit)

        ranges = data.get("ranges", {})
        phys = ranges.get("phys", {})
        self.minPhysVal = phys.get("min", self.minPhysVal)
        self.maxPhysVal = phys.get("max", self.maxPhysVal)
        self.PhySI = phys.get("si", self.PhySI)
        self.PhySIExp = phys.get("siExp", self.PhySIExp)
        self.PhyDSI = phys.get("dsi", self.PhyDSI)

        op_env = ranges.get("operatingEnvironment", {})
        self.minOperatingEnv = op_env.get("min", self.minOperatingEnv)
        self.maxOperatingEnv = op_env.get("max", self.maxOperatingEnv)
        self.OperatingEnvSI = op_env.get("si", self.OperatingEnvSI)
        self.OperatingEnvSIExp = op_env.get("siExp", self.OperatingEnvSIExp)
        self.OperatingEnvDSI = op_env.get("dsi", self.OperatingEnvDSI)

        properties = data.get("properties", {})
        self.immersionDepth = properties.get("immersionDepth", self.immersionDepth)
        self.immersionDepthSi = properties.get("immersionDepthSi", self.immersionDepthSi)
        self.immersionDepthSiExp = properties.get("immersionDepthSiExp", self.immersionDepthSiExp)
        self.immersionDepthDsi = properties.get("immersionDepthDsi", self.immersionDepthDsi)
        self.heatingTimeMax = properties.get("heatingTimeMax", self.heatingTimeMax)
        self.heatingTimeSi = properties.get("heatingTimeSi", self.heatingTimeSi)
        self.heatingTimeSiExp = properties.get("heatingTimeSiExp", self.heatingTimeSiExp)
        self.heatingTimeDsi = properties.get("heatingTimeDsi", self.heatingTimeDsi)
        self.coolingTimeMax = properties.get("coolingTimeMax", self.coolingTimeMax)
        self.coolingTimeSi = properties.get("coolingTimeSi", self.coolingTimeSi)
        self.coolingTimeSiExp = properties.get("coolingTimeSiExp", self.coolingTimeSiExp)
        self.coolingTimeDsi = properties.get("coolingTimeDsi", self.coolingTimeDsi)

        metrology = data.get("metrology", {})
        self.evaluationFormula = metrology.get("evaluationFormula", self.evaluationFormula)
        self.UncertaintyPdf = metrology.get("UncertaintyPdf", self.UncertaintyPdf)


@dataclass
class VAR_extra:
    _adc_bits: int = 16
    _U_pt_c: float = 0.065
    _k_pt: float = 2.0
    _d_tmp126_c: float = 0.30

    
