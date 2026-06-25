import base64
import json
import math
import random
import struct
from datetime import datetime, timedelta
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np



# CONFIGURABLE PARAMETERS — change these to tune the simulation


SENSOR_ID = 3
MU_ID = 2

STEPS_C = [-20.0, -10.0, 0.0, 25.0, 50.0, 75.0, 100.0, 110.0]   # 8 steps, NO 125 °C

NUM_SENSOR_PER_REF = 10                     # sensor readings per reference point
SENSOR_SAMPLING_FREQ_HZ = 1

# Noise model  (single term, matches handwritten formula N(mean, u_B))
#   sensor = ref + N(BIAS_MEAN_C, U_B_STD_C)
NOISE_BIAS_MEAN_C = 0.1                       # mean of sensor noise [°C]
U_B_STD_C = 0.5                               # Type-B std uncertainty [°C]
DISPERSION_STD_C = 0.05                       # within-group dispersion std [°C]

# NTC thermistor parameters  (Beta model)
R25 = 100000.0                                 # NTC resistance at 25 °C [Ω]
BETA = 4190.0                                 # Beta coefficient [K]
T25_K = 298.15                                # 25 °C in Kelvin
R_FIXED = 50000.0                             # fixed resistor in voltage divider [Ω]

# ADC parameters  (ratiometric:  V_ref cancels, scale = 2^16 not 2^16-1)
V_REF = 3.3                                   # reference voltage [V]  (informational)
ADC_SCALE = 2**16                             # 65536  (quantization scale, NOT ADC_MAX)
ADC_MAX = 2**16 - 1                           # 65535  (last representable code, for clipping)

# Reference data — real lab measurements (Temp_RTD column, centered around 0)
# These are the relative variations extracted from confronto.csv.
# The absolute value comes from STEPS_C, the assignment is random.
# Steps -20, -10, 110 use synthetic data (no lab data available) — see SYNTH_* below.
REF_TEMP_CENTRED_0 = np.array([
    -0.0158, -0.0174, -0.0157, -0.0162, -0.0177, -0.0176, -0.0160, -0.0179, -0.0184, -0.0190,
    -0.0182, -0.0164, -0.0149, -0.0134, -0.0117, -0.0128, -0.0141, -0.0144, -0.0153, -0.0159,
    -0.0178, -0.0178, -0.0156, -0.0183, -0.0195, -0.0203, -0.0208, -0.0225, -0.0233, -0.0237,
    -0.0232, -0.0243, -0.0247, -0.0225, -0.0218, -0.0263, -0.0276, -0.0288, -0.0291, -0.0277,
    -0.0271, -0.0243, -0.0196, -0.0196, -0.0166, -0.0149, -0.0152, -0.0148, -0.0122, -0.0104,
    -0.0109, -0.0112, -0.0100, -0.0084, -0.0096, -0.0103, -0.0122, -0.0114, -0.0095, -0.0075,
    -0.0055, -0.0037, -0.0052, -0.0052, -0.0032, -0.0045, -0.0030, -0.0020, -0.0005, -0.0012,
    -0.0042, -0.0032, -0.0018, -0.0021, -0.0042, -0.0059, -0.0059, -0.0061, -0.0059, -0.0042,
    -0.0030, -0.0043, -0.0057, -0.0043, -0.0065, -0.0086, -0.0099, -0.0089, -0.0091, -0.0091,
    -0.0105, -0.0102, -0.0088, -0.0083, -0.0072, -0.0068, -0.0054, -0.0059, -0.0065, -0.0065,
    -0.0077, -0.0107, -0.0108, -0.0115, -0.0115, -0.0105, -0.0081, -0.0048, -0.0003, -0.0019,
    0.0003, 0.0007, 0.0022, 0.0022, 0.0013, 0.0045, 0.0054, 0.0035, 0.0050, 0.0060, 0.0063,
    0.0051, 0.0029, 0.0024, 0.0039, 0.0032, 0.0073, 0.0113, 0.0120, 0.0109, 0.0123, 0.0125,
    0.0130, 0.0143, 0.0153, 0.0147, 0.0134, 0.0116, 0.0099, 0.0097, 0.0073, 0.0060, 0.0056,
    0.0054, 0.0043, 0.0027, 0.0016, 0.0036, 0.0020, 0.0015, 0.0013, -0.0005, 0.0003, 0.0014,
    0.0042, 0.0051, 0.0057, 0.0038, 0.0061, 0.0057, 0.0035, 0.0029, 0.0029, 0.0032, 0.0013,
    0.0029, 0.0042, 0.0048, 0.0041, 0.0042, 0.0032, 0.0047, 0.0050, 0.0076, 0.0111, 0.0115,
    0.0113, 0.0105, 0.0110, 0.0106, 0.0088, 0.0062, 0.0073, 0.0044, 0.0018, -0.0009, -0.0018,
    -0.0028, -0.0026, -0.0045, -0.0044, -0.0042, -0.0059, -0.0063, -0.0059, -0.0074, -0.0093,
    -0.0100, -0.0090, -0.0100, -0.0108, -0.0108, -0.0084, -0.0061, -0.0061, -0.0052, -0.0041,
    -0.0009, -0.0006, 0.0028, 0.0061, 0.0070, 0.0066, 0.0064, 0.0053, 0.0038, 0.0020, 0.0028,
    0.0022, 0.0002, -0.0020, -0.0029, -0.0020, -0.0015, -0.0029, -0.0036, -0.0009, -0.0009,
    0.0029, 0.0061, 0.0100, 0.0101, 0.0116, 0.0129, 0.0126, 0.0130, 0.0144, 0.0137, 0.0150,
    0.0138, 0.0105, 0.0097, 0.0101, 0.0094, 0.0063, 0.0058, 0.0054, 0.0042, 0.0043, 0.0022,
    0.0047, 0.0033, 0.0010, -0.0014, -0.0031, -0.0031, -0.0019, 0.0007, 0.0022, 0.0054, 0.0051,
    0.0054, 0.0062, 0.0047, 0.0056, 0.0058, 0.0077, 0.0100, 0.0107, 0.0102, 0.0098, 0.0115,
    0.0119, 0.0125, 0.0150, 0.0133, 0.0141, 0.0136, 0.0140, 0.0116, 0.0085, 0.0065, 0.0067,
    0.0052, 0.0051, 0.0082, 0.0128, 0.0154, 0.0157, 0.0155, 0.0145, 0.0142, 0.0139, 0.0122,
    0.0125, 0.0127, 0.0120, 0.0110, 0.0102, 0.0123, 0.0106, 0.0117, 0.0134, 0.0120, 0.0104,
    0.0087, 0.0089, 0.0091, 0.0063, 0.0030, -0.0005, -0.0025, -0.0032, -0.0027, -0.0021, 0.0010,
    0.0026, 0.0047, 0.0052, 0.0073, 0.0093, 0.0099, 0.0107, 0.0131, 0.0150, 0.0151, 0.0162,
    0.0188, 0.0201, 0.0179, 0.0213, 0.0210, 0.0188, 0.0175, 0.0169, 0.0142, 0.0109, 0.0095,
])

REF_TEMP_CENTRED_25 = np.array([
    -0.0136, -0.0144, -0.0141, -0.0144, -0.0121, -0.0114, -0.0119, -0.0068, -0.0074, -0.0070,
    -0.0049, -0.0039, -0.0023, -0.0013, 0.0006, 0.0031, 0.0036, 0.0059, 0.0071, 0.0087,
    0.0108, 0.0110, 0.0129, 0.0113, 0.0139, 0.0145, 0.0144, 0.0141, 0.0144, 0.0166,
    0.0184, 0.0190, 0.0197, 0.0202, 0.0185, 0.0190, 0.0191, 0.0184, 0.0171, 0.0145,
    0.0130, 0.0104, 0.0116, 0.0107, 0.0085, 0.0076, 0.0062, 0.0070, 0.0083, 0.0080,
    0.0083, 0.0092, 0.0124, 0.0129, 0.0152, 0.0173, 0.0188, 0.0216, 0.0225, 0.0253,
    0.0278, 0.0294, 0.0309, 0.0329, 0.0346, 0.0350, 0.0365, 0.0375, 0.0394, 0.0395,
    0.0396, 0.0388, 0.0389, 0.0383, 0.0377, 0.0358, 0.0361, 0.0348, 0.0339, 0.0342,
    0.0336, 0.0315, 0.0309, 0.0317, 0.0320, 0.0313, 0.0294, 0.0268, 0.0278, 0.0251,
    0.0240, 0.0211, 0.0201, 0.0188, 0.0161, 0.0129, 0.0116, 0.0077, 0.0072, 0.0062,
    0.0040, 0.0044, 0.0028, 0.0028, 0.0032, 0.0045, 0.0050, 0.0058, 0.0054, 0.0077,
    0.0081, 0.0085, 0.0090, 0.0100, 0.0105, 0.0106, 0.0107, 0.0106, 0.0097, 0.0067,
    0.0069, 0.0039, 0.0046, 0.0029, 0.0037, 0.0044, 0.0052, 0.0051, 0.0070, 0.0076,
    0.0103, 0.0116, 0.0122, 0.0129, 0.0145, 0.0142, 0.0125, 0.0115, 0.0129, 0.0127,
    0.0112, 0.0110, 0.0090, 0.0081, 0.0069, 0.0063, 0.0061, 0.0047, 0.0030, 0.0026,
    0.0016, 0.0035, 0.0039, 0.0016, 0.0039, 0.0045, 0.0050, 0.0055, 0.0063, 0.0055,
    0.0055, 0.0052, 0.0015, -0.0007, -0.0042, -0.0051, -0.0070, -0.0100, -0.0132, -0.0145,
    -0.0157, -0.0156, -0.0152, -0.0157, -0.0132, -0.0114, -0.0100, -0.0068, -0.0049, -0.0018,
    0.0003, 0.0029, 0.0049, 0.0075, 0.0097, 0.0118, 0.0136, 0.0167, 0.0175, 0.0185,
    0.0190, 0.0220, 0.0233, 0.0230, 0.0226, 0.0234, 0.0242, 0.0238, 0.0218, 0.0211,
    0.0200, 0.0170, 0.0171, 0.0168, 0.0157, 0.0114, 0.0110, 0.0090, 0.0075, 0.0055,
    0.0063, 0.0046, 0.0026, 0.0029, 0.0015, 0.0007, 0.0005, -0.0012, -0.0035, -0.0044,
    -0.0042, -0.0047, -0.0059, -0.0046, -0.0063, -0.0049, -0.0030, -0.0032, -0.0034, -0.0022,
    0.0009, -0.0003, 0.0032, 0.0010, 0.0022, 0.0022, 0.0007, -0.0012, -0.0023, -0.0024,
    -0.0037, -0.0032, -0.0050, -0.0035, -0.0033, -0.0009, 0.0003, 0.0002, 0.0016, 0.0027,
    0.0016, 0.0003, 0.0017, 0.0003, -0.0013, -0.0034, -0.0049, -0.0071, -0.0079, -0.0080,
    -0.0068, -0.0064, -0.0067, -0.0070, -0.0066, -0.0056, -0.0042, -0.0038, -0.0047, -0.0021,
    -0.0016, -0.0007, -0.0009, 0.0009, 0.0014, 0.0019, 0.0033, 0.0026, 0.0025, 0.0021,
    0.0028, 0.0027, 0.0033, 0.0040, 0.0024, 0.0012, 0.0013, 0.0010, 0.0003, 0.0024,
    0.0014, 0.0009, 0.0016, 0.0019, 0.0033, 0.0003, -0.0008, -0.0015, -0.0042, -0.0045,
    -0.0051, -0.0064, -0.0059, -0.0074, -0.0100, -0.0108, -0.0104, -0.0095, -0.0106, -0.0103,
    -0.0105, -0.0103, -0.0108, -0.0091, -0.0095, -0.0088, -0.0093, -0.0075, -0.0055, -0.0064,
    -0.0057, -0.0058, -0.0040, -0.0016, -0.0023, -0.0031, -0.0035, -0.0050, -0.0043, -0.0055,
    -0.0055, -0.0056, -0.0042, -0.0037, -0.0028, -0.0023, -0.0007, -0.0028, -0.0048, -0.0058,
    -0.0063, -0.0090, -0.0112, -0.0128, -0.0152, -0.0173, -0.0183, -0.0202, -0.0196, -0.0215,
    -0.0204, -0.0203, -0.0188, -0.0187, -0.0193, -0.0172, -0.0151, -0.0146, -0.0142, -0.0130,
    -0.0122, -0.0114, -0.0133, -0.0135, -0.0131, -0.0145, -0.0137, -0.0138, -0.0132, -0.0127,
    -0.0127, -0.0137, -0.0134, -0.0137, -0.0143, -0.0138, -0.0133, -0.0135, -0.0135, -0.0137,
    -0.0136, -0.0126, -0.0122, -0.0115, -0.0089, -0.0109, -0.0094, -0.0084, -0.0075, -0.0085,
    -0.0090, -0.0090, -0.0080, -0.0080, -0.0076, -0.0070, -0.0080, -0.0082, -0.0080, -0.0084,
    -0.0091, -0.0090, -0.0091, -0.0103, -0.0096, -0.0096, -0.0091, -0.0079, -0.0097, -0.0083,
    -0.0081, -0.0085, -0.0076, -0.0082, -0.0084, -0.0079, -0.0084, -0.0084, -0.0087, -0.0072,
    -0.0070, -0.0075, -0.0081, -0.0088, -0.0110, -0.0106, -0.0109, -0.0126, -0.0130, -0.0141,
    -0.0151, -0.0161, -0.0162, -0.0154, -0.0156, -0.0168, -0.0177, -0.0195, -0.0213, -0.0216,
    -0.0219, -0.0224, -0.0220, -0.0244, -0.0229, -0.0242, -0.0240, -0.0225, -0.0228, -0.0232,
    -0.0241, -0.0233, -0.0238, -0.0212, -0.0209, -0.0202, -0.0194, -0.0194, -0.0204, -0.0188,
    -0.0197, -0.0190, -0.0209, -0.0209, -0.0199, -0.0203, -0.0190, -0.0211, -0.0206, -0.0211,
    -0.0198, -0.0206, -0.0205, -0.0210, -0.0216, -0.0215, -0.0235,
])

REF_TEMP_CENTRED_50 = np.array([
    0.0057, 0.0040, 0.0039, 0.0043, 0.0044, 0.0033, 0.0037, 0.0047, 0.0040, 0.0042,
    0.0042, 0.0047, 0.0053, 0.0057, 0.0044, 0.0059, 0.0050, 0.0050, 0.0056, 0.0048,
    0.0054, 0.0048, 0.0046, 0.0053, 0.0052, 0.0037, 0.0046, 0.0053, 0.0046, 0.0041,
    0.0050, 0.0039, 0.0050, 0.0046, 0.0035, 0.0028, 0.0038, 0.0027, 0.0024, 0.0025,
    0.0019, -0.0002, 0.0022, 0.0010, 0.0019, 0.0011, 0.0022, 0.0008, 0.0013, 0.0015,
    0.0025, 0.0029, 0.0010, 0.0023, 0.0020, 0.0017, 0.0013, 0.0002, 0.0017, 0.0013,
    0.0001, 0.0019, 0.0010, 0.0002, 0.0011, 0.0015, 0.0010, 0.0014, 0.0006, 0.0006,
    0.0010, 0.0017, 0.0007, 0.0019, 0.0016, 0.0002, 0.0006, 0.0018, 0.0021, 0.0015,
    0.0008, 0.0021, 0.0017, -0.0002, 0.0009, -0.0009, -0.0000, 0.0001, 0.0005, 0.0004,
    -0.0000, 0.0008, -0.0000, 0.0010, 0.0007, -0.0006, 0.0002, 0.0009, -0.0016, -0.0011,
    0.0004, 0.0002, -0.0006, -0.0000, -0.0006, 0.0014, 0.0002, 0.0003, -0.0009, -0.0011,
    -0.0002, -0.0006, -0.0014, -0.0010, -0.0012, -0.0014, -0.0006, 0.0003, -0.0002, 0.0004,
    0.0004, 0.0010, 0.0016, 0.0017, 0.0001, 0.0008, 0.0008, 0.0007, 0.0004, 0.0014,
    0.0012, 0.0003, 0.0013, 0.0009, 0.0024, 0.0022, 0.0023, 0.0016, 0.0022, 0.0016,
    0.0022, 0.0030, 0.0027, 0.0022, 0.0029, 0.0027, 0.0030, 0.0024, 0.0029, 0.0026,
    0.0034, 0.0041, 0.0035, 0.0033, 0.0011, 0.0011, 0.0003, -0.0005, 0.0004, -0.0022,
    -0.0010, -0.0024, -0.0033, -0.0047, -0.0043, -0.0055, -0.0078, -0.0066, -0.0083, -0.0071,
    -0.0073, -0.0086, -0.0090, -0.0083, -0.0092, -0.0101, -0.0113, -0.0104, -0.0102, -0.0109,
    -0.0117, -0.0105, -0.0110, -0.0117, -0.0116, -0.0118, -0.0120, -0.0120, -0.0118, -0.0118,
    -0.0133, -0.0113, -0.0129, -0.0115, -0.0126, -0.0100, -0.0113, -0.0114, -0.0101, -0.0102,
    -0.0122, -0.0116, -0.0119, -0.0118, -0.0119, -0.0105, -0.0089, -0.0095, -0.0078, -0.0090,
    -0.0081, -0.0059, -0.0068, -0.0047, -0.0043, -0.0035, -0.0035, -0.0026, -0.0030, -0.0010,
    -0.0006, 0.0005, 0.0002, -0.0004, 0.0004, 0.0005, -0.0002, -0.0003, -0.0000, 0.0006,
    0.0005, 0.0002, 0.0007, 0.0004, 0.0007, 0.0024, 0.0007, 0.0006, 0.0019, 0.0024,
    0.0021, 0.0013, 0.0024, 0.0013, 0.0017, 0.0021, 0.0017, 0.0023, 0.0027, 0.0035,
    0.0030, 0.0033, 0.0025, 0.0027, 0.0039, 0.0034, 0.0032, 0.0030, 0.0040, 0.0036,
    0.0035, 0.0035, 0.0046, 0.0043, 0.0044, 0.0056, 0.0047, 0.0043, 0.0039, 0.0037,
    0.0041, 0.0040, 0.0033, 0.0023, 0.0023, 0.0028, 0.0021, 0.0009, 0.0014, 0.0016,
    0.0007, 0.0009, 0.0013, 0.0011, 0.0017, 0.0009, 0.0014, 0.0004, 0.0014, 0.0019,
    0.0020, 0.0028, 0.0024, 0.0024, 0.0019, 0.0024, 0.0036, 0.0025, 0.0030, 0.0029,
    0.0033, 0.0027, 0.0013, 0.0030, 0.0025, 0.0030, 0.0031, 0.0025, 0.0027, 0.0031,
    0.0023, 0.0036, 0.0027, 0.0027, 0.0033, 0.0040, 0.0030, 0.0033, 0.0033, 0.0024,
    0.0017, 0.0012,
])

REF_TEMP_CENTRED_75 = np.array([
    -0.0163, -0.0171, -0.0194, -0.0163, -0.0171, -0.0139, -0.0163, -0.0155, -0.0147, -0.0147,
    -0.0155, -0.0139, -0.0116, -0.0124, -0.0116, -0.0139, -0.0124, -0.0139, -0.0124, -0.0108,
    -0.0116, -0.0116, -0.0116, -0.0108, -0.0124, -0.0116, -0.0132, -0.0092, -0.0116, -0.0092,
    -0.0124, -0.0100, -0.0085, -0.0092, -0.0092, -0.0085, -0.0069, -0.0085, -0.0069, -0.0092,
    -0.0069, -0.0085, -0.0061, -0.0069, -0.0069, -0.0069, -0.0061, -0.0069, -0.0069, -0.0077,
    -0.0053, -0.0061, -0.0030, -0.0061, -0.0038, -0.0045, -0.0061, -0.0077, -0.0069, -0.0061,
    -0.0053, -0.0045, -0.0061, -0.0061, -0.0053, -0.0061, -0.0053, -0.0022, -0.0038, -0.0061,
    -0.0030, -0.0069, -0.0030, -0.0061, -0.0045, -0.0053, -0.0061, -0.0038, -0.0045, -0.0045,
    -0.0038, -0.0045, -0.0053, -0.0022, -0.0045, -0.0038, -0.0038, -0.0030, -0.0009, -0.0022,
    -0.0014, -0.0030, -0.0014, -0.0022, -0.0053, -0.0030, -0.0014, -0.0014, -0.0001, -0.0014,
    0.0007, -0.0022, -0.0014, -0.0038, -0.0014, -0.0014, -0.0014, -0.0022, -0.0045, -0.0038,
    -0.0022, -0.0022, -0.0022, -0.0014, -0.0038, -0.0030, -0.0014, -0.0038, -0.0014, 0.0007,
    -0.0014, -0.0009, -0.0022, -0.0001, -0.0022, -0.0009, 0.0007, 0.0015, -0.0001, 0.0007,
    -0.0030, -0.0001, 0.0023, -0.0014, -0.0022, -0.0022, -0.0014, 0.0007, -0.0014, -0.0022,
    -0.0009, 0.0007, -0.0022, -0.0001, 0.0023, 0.0007, -0.0001, 0.0015, -0.0001, -0.0001,
    0.0007, 0.0015, 0.0023, 0.0030, 0.0030, -0.0009, 0.0015, 0.0023, 0.0046, 0.0023,
    -0.0001, -0.0001, 0.0030, 0.0015, 0.0007, 0.0038, 0.0038, 0.0007, -0.0009, -0.0009,
    0.0007, 0.0007, -0.0001, 0.0023, -0.0014, 0.0023, 0.0015, 0.0030, 0.0023, 0.0015,
    0.0023, 0.0023, 0.0023, 0.0015, 0.0023, 0.0038, 0.0030, 0.0038, 0.0046, 0.0054,
    0.0046, 0.0038, 0.0054, 0.0062, 0.0062, 0.0046, 0.0070, 0.0054, 0.0046, 0.0077,
    0.0062, 0.0054, 0.0070, 0.0054, 0.0070, 0.0093, 0.0054, 0.0054, 0.0070, 0.0077,
    0.0070, 0.0062, 0.0077, 0.0062, 0.0077, 0.0070, 0.0070, 0.0093, 0.0093, 0.0109,
    0.0101, 0.0085, 0.0109, 0.0109, 0.0101, 0.0101, 0.0109, 0.0109, 0.0132, 0.0140,
    0.0093, 0.0109, 0.0132, 0.0132, 0.0125, 0.0140, 0.0148, 0.0156, 0.0140, 0.0132,
    0.0125, 0.0140, 0.0140, 0.0101, 0.0125, 0.0132, 0.0117, 0.0132, 0.0148, 0.0125,
    0.0132, 0.0125, 0.0140, 0.0148, 0.0125, 0.0140, 0.0125, 0.0109, 0.0109, 0.0125,
    0.0148, 0.0132, 0.0140, 0.0117,
])

REF_TEMP_CENTRED_100 = np.array([
    -0.0167, -0.0167, -0.0175, -0.0143, -0.0127, -0.0143, -0.0112, -0.0104, -0.0127, -0.0096,
    -0.0104, -0.0080, -0.0096, -0.0088, -0.0064, -0.0072, -0.0072, -0.0088, -0.0080, -0.0088,
    -0.0112, -0.0080, -0.0096, -0.0064, -0.0080, -0.0072, -0.0104, -0.0080, -0.0112, -0.0104,
    -0.0104, -0.0088, -0.0088, -0.0135, -0.0120, -0.0120, -0.0112, -0.0135, -0.0112, -0.0112,
    -0.0112, -0.0104, -0.0112, -0.0112, -0.0120, -0.0104, -0.0112, -0.0104, -0.0088, -0.0072,
    -0.0080, -0.0088, -0.0080, -0.0064, -0.0033, -0.0048, -0.0056, -0.0033, -0.0025, -0.0025,
    -0.0017, 0.0007, 0.0007, -0.0017, 0.0028, 0.0015, 0.0023, 0.0007, -0.0001, -0.0001,
    -0.0001, -0.0001, -0.0009, -0.0009, 0.0007, -0.0017, -0.0025, -0.0033, -0.0041, -0.0048,
    -0.0048, -0.0048, -0.0041, -0.0064, -0.0064, -0.0096, -0.0064, -0.0080, -0.0072, -0.0080,
    -0.0080, -0.0096, -0.0104, -0.0088, -0.0088, -0.0088, -0.0080, -0.0056, -0.0056, -0.0056,
    -0.0041, -0.0033, -0.0033, -0.0017, -0.0009, 0.0007, -0.0009, 0.0007, 0.0015, 0.0023,
    0.0028, 0.0023, 0.0036, 0.0007, 0.0015, 0.0007, -0.0001, 0.0015, -0.0009, 0.0023,
    -0.0009, 0.0015, 0.0007, 0.0015, 0.0028, -0.0009, 0.0023, -0.0001, -0.0017, -0.0001,
    -0.0025, -0.0033, -0.0041, -0.0072, -0.0041, -0.0033, -0.0041, -0.0048, -0.0041, -0.0056,
    -0.0041, -0.0056, -0.0041, -0.0064, -0.0064, -0.0064, -0.0072, -0.0080, -0.0048, -0.0041,
    -0.0033, -0.0041, -0.0025, -0.0033, -0.0009, 0.0028, 0.0007, 0.0023, 0.0028, 0.0036,
    0.0036, 0.0036, 0.0052, 0.0052, 0.0052, 0.0044, 0.0044, 0.0044, 0.0075, 0.0059,
    0.0052, 0.0075, 0.0036, 0.0059, 0.0036, 0.0036, 0.0036, 0.0036, 0.0023, 0.0015,
    0.0028, -0.0009, 0.0007, -0.0009, -0.0001, -0.0009, -0.0009, -0.0041, -0.0017, -0.0033,
    -0.0017, -0.0033, -0.0033, -0.0048, -0.0033, -0.0048, -0.0033, -0.0041, -0.0033, -0.0033,
    -0.0041, -0.0041, -0.0009, -0.0025, -0.0041, -0.0017, -0.0009, -0.0001, -0.0009, 0.0007,
    0.0044, 0.0044, 0.0075, 0.0075, 0.0052, 0.0059, 0.0067, 0.0052, 0.0059, 0.0083,
    0.0091, 0.0099, 0.0091, 0.0099, 0.0091, 0.0099, 0.0099, 0.0099, 0.0107, 0.0138,
    0.0123, 0.0154, 0.0154, 0.0146, 0.0146, 0.0123, 0.0107, 0.0091, 0.0115, 0.0075,
    0.0091, 0.0067, 0.0075, 0.0067, 0.0059, 0.0075, 0.0067, 0.0067, 0.0091, 0.0052,
    0.0059, 0.0075, 0.0083, 0.0115, 0.0083, 0.0091, 0.0091, 0.0075, 0.0083, 0.0075,
    0.0099, 0.0091, 0.0091, 0.0115, 0.0123, 0.0131, 0.0099, 0.0107, 0.0131, 0.0107,
    0.0115, 0.0131, 0.0099, 0.0099, 0.0107, 0.0099, 0.0115, 0.0107, 0.0123, 0.0115,
    0.0099, 0.0131, 0.0123, 0.0146, 0.0146, 0.0131, 0.0131, 0.0123, 0.0146, 0.0131,
    0.0115,
])

# Synthetic centred profiles for steps without lab data (-20, -10, 110)
SYN_N_SAMPLES = 300
SYN_AMP = 0.03
SYN_PERIOD = 60
SYN_NOISE_STD = 0.015
_rng_static = np.random.default_rng(123)
_ref_seed = {
    "N20": _rng_static.standard_normal(SYN_N_SAMPLES),
    "N10": _rng_static.standard_normal(SYN_N_SAMPLES),
    "110": _rng_static.standard_normal(SYN_N_SAMPLES),
}
_REF_TEMP_CENTRED_SYN = {
    key: SYN_AMP * np.sin(2.0 * np.pi * np.arange(SYN_N_SAMPLES) / SYN_PERIOD)
    + SYN_NOISE_STD * _ref_seed[key]
    for key in _ref_seed
}

# Lookup used during simulation: step label -> centred series
CENTRED_REF_DATA: dict[str, np.ndarray] = {
    "-20": _REF_TEMP_CENTRED_SYN["N20"],
    "-10": _REF_TEMP_CENTRED_SYN["N10"],
    "0":   REF_TEMP_CENTRED_0,
    "25":  REF_TEMP_CENTRED_25,
    "50":  REF_TEMP_CENTRED_50,
    "75":  REF_TEMP_CENTRED_75,
    "100": REF_TEMP_CENTRED_100,
    "110": _REF_TEMP_CENTRED_SYN["110"],
}
STEP_LABELS = list(CENTRED_REF_DATA.keys())

RNG_SEED = 42

PLOT_MAX_POINTS = 500                           # downsample for scatter plots
PLOT_MARKER_SIZE = 6

OUTPUT_DIR = Path(__file__).resolve().parent
OUTPUT_FILE = OUTPUT_DIR / "one_measure.json"
PLOT_FILE_1 = OUTPUT_DIR / "one_measure_scatter_ref_sens.png"
PLOT_FILE_2 = OUTPUT_DIR / "one_measure_ntc_lsb.png"



# Helpers



def load_ref_temperatures() -> list[np.ndarray]:
    """Return centred reference temperature series in STEP_LABELS order."""
    return [CENTRED_REF_DATA[label] for label in STEP_LABELS]


def make_calib_id(sensor_id: int, mu_id: int) -> str:
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    return f"calib-{sensor_id}-{mu_id}-{ts}"


def encode_sensor_b64(values: list[int]) -> str:
    if not values:
        return ""
    packed = struct.pack(f"<{len(values)}H", *values)
    return base64.b64encode(packed).decode("ascii")



# NTC → LSB conversion  (encoding direction, ratiometric)
#   V_out/V_ref = R_FIXED / (R_FIXED + R_ntc)  →  V_ref cancels
#   LSB = (2^16) · R_FIXED / (R_FIXED + R(T))  →  use ADC_SCALE not ADC_MAX


def temp_to_ntc_resistance(temp_c: float | np.ndarray) -> float | np.ndarray:
    t_k = temp_c + 273.15
    return R25 * np.exp(BETA * (1.0 / t_k - 1.0 / T25_K))


def ntc_to_voltage(r_ntc: float | np.ndarray) -> float | np.ndarray:
    """Informational only — kept for plotting."""
    return V_REF * R_FIXED / (R_FIXED + r_ntc)


def resistance_to_lsb(r_ntc: float | np.ndarray) -> int | np.ndarray:
    """Ratiometric:  LSB = (2^16) · R_FIXED / (R_FIXED + R_ntc)."""
    lsb = ADC_SCALE * R_FIXED / (R_FIXED + r_ntc)
    if isinstance(lsb, np.ndarray):
        return np.rint(lsb).astype(int).clip(0, ADC_MAX)
    return int(round(max(0, min(lsb, ADC_MAX))))


def temp_c_to_lsb(temp_c: float | np.ndarray) -> int | np.ndarray:
    r = temp_to_ntc_resistance(temp_c)
    return resistance_to_lsb(r)



# Decoding helpers (for verification / plotting)


def lsb_to_voltage(lsb: int | np.ndarray) -> float | np.ndarray:
    if isinstance(lsb, np.ndarray):
        return (lsb.astype(float) / ADC_SCALE) * V_REF
    return (float(lsb) / ADC_SCALE) * V_REF


def lsb_to_temp_c(lsb: int | np.ndarray) -> float | np.ndarray:
    """Inverse: LSB → °C via Beta model + voltage divider.

    LSB = (2^16) · R_FIXED / (R_FIXED + R_ntc)
    →  R_ntc = R_FIXED · (ADC_SCALE/LSB − 1)
    →  R_ntc/R25 = (R_FIXED/R25) · (ADC_SCALE/LSB − 1)
    →  1/T = 1/T25 + (1/B)·ln(R_ntc/R25)
    """
    if isinstance(lsb, np.ndarray):
        denom = np.where(lsb > 0, ADC_SCALE / lsb.astype(float) - 1.0, 1e-12)
        ratio = np.maximum(denom, 1e-12)
        r_ratio = (R_FIXED / R25) * ratio
        l = np.log(r_ratio)
        t_k = 1.0 / (1.0 / T25_K + l / BETA)
        return t_k - 273.15
    if lsb <= 0:
        return -273.15
    ratio = ADC_SCALE / float(lsb) - 1.0
    if ratio <= 0:
        return -273.15
    r_ratio = (R_FIXED / R25) * ratio
    t_k = 1.0 / (1.0 / T25_K + math.log(r_ratio) / BETA)
    return t_k - 273.15



# Main simulation


def simulate():
    rng = np.random.default_rng(RNG_SEED)
    random.seed(RNG_SEED)


    # 1. Load centred reference temperature profiles (in-memory)

    centred_ref_series = load_ref_temperatures()


    # 2. Randomly assign centred profiles to step target temperatures
    

    n_steps = len(STEPS_C)
    assert len(centred_ref_series) == n_steps, (
        f"Expected {n_steps} reference data series, got {len(centred_ref_series)}"
    )
    assignment = list(range(n_steps))
    random.shuffle(assignment)

    print("Step assignment (ref_data_index -> step_target):")
    for step_i, ref_i in enumerate(assignment):
        print(f"  step {step_i}: target={STEPS_C[step_i]:6.1f}°C  "
              f"<- ref data from  key \"{STEP_LABELS[ref_i]}\"")

    # Build absolute reference temperatures per step
    step_ref_temps = []
    for step_i, ref_i in enumerate(assignment):
        step_ref_temps.append(STEPS_C[step_i] + centred_ref_series[ref_i])


    # 3. Generate sensor temperatures with noise
    #    sensor = ref + systematic_error + N(mean, std)
    #    + 100 readings per reference  (with within-group dispersion)

    now = datetime.now().replace(microsecond=0)
    calib_id = make_calib_id(SENSOR_ID, MU_ID)

    step_summary = [
        {"target": t, "minutes": 1}
        for t in STEPS_C
    ]
    overall_start = now.isoformat() + "Z"

    messages = []
    all_ref_c = []
    all_sensor_c = []
    all_sensor_lsb = []
    all_voltage = []

    for step_idx, (target, ref_temps) in enumerate(zip(STEPS_C, step_ref_temps)):
        n_ref = len(ref_temps)
        ref_readings = ref_temps.tolist()

        # sensor_temp = ref + N(mean=NOISE_BIAS_MEAN, std=U_B)   [single term, ratiometric model]
        sensor_means = ref_temps + rng.normal(NOISE_BIAS_MEAN_C, U_B_STD_C, size=n_ref)

        # 100 sensor values per reference, with within-group dispersion
        sensor_values = []
        for sm in sensor_means:
            frame = rng.normal(sm, DISPERSION_STD_C, size=NUM_SENSOR_PER_REF)
            # Convert each sensor temperature to LSB
            frame_lsb = temp_c_to_lsb(frame)
            sensor_values.extend(frame_lsb.tolist())

        sensor_b64 = encode_sensor_b64(sensor_values)

        dwell_dt = now + timedelta(seconds=0, milliseconds=200)
        start_time_dwell = dwell_dt.isoformat() + "Z"

        msg = {
            "calib_id": calib_id,
            "target": target,
            "step_summary": step_summary,
            "step_index": step_idx,
            "start_time": overall_start,
            "start_time_dwell": start_time_dwell,
            "ref_readings": [round(float(r), 6) for r in ref_readings],
            "sensor_sampling_freq": SENSOR_SAMPLING_FREQ_HZ,
            "sensor_b64": sensor_b64,
        }
        messages.append(msg)

        # Accumulate for plotting  (sensor temps decoded from LSB for verification)
        for i, r in enumerate(ref_temps):
            all_ref_c.extend([float(r)] * NUM_SENSOR_PER_REF)
            frame_start = i * NUM_SENSOR_PER_REF
            frame_end = frame_start + NUM_SENSOR_PER_REF
            frame_lsb_vals = sensor_values[frame_start:frame_end]
            frame_c = lsb_to_temp_c(np.array(frame_lsb_vals, dtype=float))
            frame_v = lsb_to_voltage(np.array(frame_lsb_vals, dtype=float))
            all_sensor_c.extend(frame_c.tolist())
            all_voltage.extend(frame_v.tolist())
            all_sensor_lsb.extend(frame_lsb_vals)


    # 4. Write JSON output

    OUTPUT_FILE.write_text(json.dumps(messages, indent=2), encoding="utf-8")
    print(f"\ncalib_id : {calib_id}")
    print(f"steps    : {len(messages)}")
    print(f"LSB range: [0, {ADC_MAX}]")
    for m in messages:
        print(f"  step {m['step_index']}: target={m['target']:6.1f}C  "
              f"ref={len(m['ref_readings']):4d} readings  "
              f"sensor_b64={len(m['sensor_b64'])} chars")
    print(f"written  : {OUTPUT_FILE}")


    # 5. Statistics

    ref_arr = np.array(all_ref_c, dtype=float)
    sens_arr = np.array(all_sensor_c, dtype=float)
    lsb_arr = np.array(all_sensor_lsb, dtype=float)
    volt_arr = np.array(all_voltage, dtype=float)
    residuals_c = sens_arr - ref_arr
    residuals_k = (sens_arr + 273.15) - (ref_arr + 273.15)  # same as °C diff

    print(f"\nresiduals (°C): mean={residuals_c.mean():.6f}  std={residuals_c.std():.6f}  "
          f"min={residuals_c.min():.6f}  max={residuals_c.max():.6f}")
    print(f"total points  : {len(ref_arr)}")

    # Downsample for plotting performance
    rng_plot = np.random.default_rng(RNG_SEED)
    n_total = len(ref_arr)
    if n_total > PLOT_MAX_POINTS:
        idx = rng_plot.choice(n_total, size=PLOT_MAX_POINTS, replace=False)
        idx.sort()
        ref_p = ref_arr[idx]
        sens_p = sens_arr[idx]
        lsb_p = lsb_arr[idx]
        volt_p = volt_arr[idx]
        resid_p = residuals_c[idx]
        print(f"plot subsample: {PLOT_MAX_POINTS} of {n_total}")
    else:
        ref_p, sens_p, lsb_p, volt_p, resid_p = ref_arr, sens_arr, lsb_arr, volt_arr, residuals_c


    # 6. Plot 1 — Sensor vs Reference  (°C)

    fig1, (ax1, ax2) = plt.subplots(1, 2, figsize=(18, 7))

    ax1.scatter(sens_p, ref_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:blue", edgecolors="none")
    ax1.set_xlabel("sensor °C  (decoded LSB → NTC → °C)")
    ax1.set_ylabel("ref °C")
    ax1.set_title(f"sensor vs ref   ({calib_id})")
    ax1.grid(True, alpha=0.3)

    lim_min = min(ax1.get_xlim()[0], ax1.get_ylim()[0])
    lim_max = max(ax1.get_xlim()[1], ax1.get_ylim()[1])
    ax1.plot([lim_min, lim_max], [lim_min, lim_max], "k--", linewidth=0.8, alpha=0.4)
    ax1.set_xlim(lim_min, lim_max)
    ax1.set_ylim(lim_min, lim_max)

    ax2.scatter(ref_p, resid_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:red", edgecolors="none")
    ax2.axhline(y=0, color="k", linewidth=0.8, linestyle="--", alpha=0.4)
    ax2.set_xlabel("ref °C")
    ax2.set_ylabel("residual  sensor − ref  (°C)")
    ax2.set_title("residuals  (sensor − ref)")
    ax2.grid(True, alpha=0.3)

    fig1.tight_layout()
    fig1.savefig(str(PLOT_FILE_1), dpi=150)
    plt.show(block=False)
    print(f"plot 1  : {PLOT_FILE_1}")


    # 7. Plot 2 — NTC → LSB / Voltage analysis
    #    T_ref − T_sens (K), voltage vs ref, LSB vs ref

    ref_k_p = ref_p + 273.15
    diff_k_p = ref_k_p - (sens_p + 273.15)

    fig2, axes = plt.subplots(2, 2, figsize=(18, 12))

    # Top-left: T_ref − T_sens in Kelvin
    ax = axes[0, 0]
    ax.scatter(ref_k_p, diff_k_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:orange", edgecolors="none")
    ax.axhline(y=0, color="k", linewidth=0.8, linestyle="--", alpha=0.4)
    ax.set_xlabel("T_ref  (K)")
    ax.set_ylabel("T_ref − T_sens  (K)")
    ax.set_title("T_ref − T_sens  (Kelvin domain)")
    ax.grid(True, alpha=0.3)

    # Top-right: Voltage vs reference temperature
    ax = axes[0, 1]
    ax.scatter(ref_p, volt_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:green", edgecolors="none")
    ax.set_xlabel("ref °C")
    ax.set_ylabel("V_out  (V)")
    ax.set_title(f"Voltage divider output  (R_fixed={R_FIXED} Ω, R25={R25} Ω)")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(0, V_REF * 1.02)

    # Bottom-left: LSB vs reference temperature
    ax = axes[1, 0]
    ax.scatter(ref_p, lsb_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:purple", edgecolors="none")
    ax.set_xlabel("ref °C")
    ax.set_ylabel("LSB  (0 – 65535)")
    ax.set_title("LSB vs ref °C")
    ax.grid(True, alpha=0.3)
    ax.set_ylim(-500, ADC_MAX + 500)

    # Bottom-right: Voltage vs LSB  (transfer characteristic)
    ax = axes[1, 1]
    ax.scatter(lsb_p, volt_p, s=PLOT_MARKER_SIZE, alpha=0.5, c="tab:brown", edgecolors="none")
    ax.plot([0, ADC_MAX], [0, V_REF], "k--", linewidth=0.8, alpha=0.4, label="ideal linear")
    ax.set_xlabel("LSB")
    ax.set_ylabel("V_out  (V)")
    ax.set_title("Voltage vs LSB")
    ax.legend()
    ax.grid(True, alpha=0.3)

    fig2.tight_layout()
    fig2.savefig(str(PLOT_FILE_2), dpi=150)
    plt.show()
    print(f"plot 2  : {PLOT_FILE_2}")


if __name__ == "__main__":
    simulate()
