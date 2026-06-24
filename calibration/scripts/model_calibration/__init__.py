# model_calibration package — calibration engine modules
# Each module exposes: calibrate(), plot_charts(), save_charts(), build_report()
#
# Available models:
#   linear_calibration    — OLS linear regression (polynomial degree 1)
#   quadratic_calibration — OLS quadratic polynomial regression (polynomial degree 2)
#   cubic_calibration     — OLS cubic polynomial regression (polynomial degree 3)
#   steinhart_calibration — OLS Steinhart-Hart in resistance domain (3 coefficients)
#   calib_plots           — unified chart generator (bundles for all procedures)
#   unit_checks           — dimensional analysis via pint
