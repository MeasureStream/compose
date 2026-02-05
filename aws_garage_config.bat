@echo off
REM AWS CLI Configuration for Garage S3 (Windows Batch)
REM Run this file to configure AWS CLI for Garage S3 access
REM Usage: aws_garage_config.bat

set AWS_ACCESS_KEY_ID=GKe8afbd2d80a1ee88b135c9cf
set AWS_SECRET_ACCESS_KEY=0b432bb89dc684efc3793bb32c6b696e53576c0fff8a03f61077e7e02b01c34b
set AWS_DEFAULT_REGION=garage
set AWS_ENDPOINT_URL=http://100.127.76.43:3900

echo AWS CLI configured for Garage S3 at %AWS_ENDPOINT_URL%
echo Key ID: %AWS_ACCESS_KEY_ID%
echo Region: %AWS_DEFAULT_REGION%
echo.
echo You can now use AWS CLI commands like:
echo aws s3 ls
echo aws s3 cp file.txt s3://bucket-name/
echo.