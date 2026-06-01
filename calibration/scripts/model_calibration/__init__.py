# model_calibration package — calibration engine modules
# Each module exposes: calibrate(), plot_charts(), save_charts(), build_report()
#
# Available models:
#   linear_calibration       — OLS linear regression (polynomial degree 1)
#   cubic_calibration        — OLS cubic polynomial regression (polynomial degree 3)
#   cubic_interp_calibration — Lagrange cubic interpolation (exactly 4 nodes, zero residual at nodes)
#   linear_interp_calibration— Piecewise linear interpolation (any number of nodes >= 2)
