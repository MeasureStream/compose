# AWS CLI Configuration for Garage S3 (PowerShell)
# Run this script to configure AWS CLI for Garage S3 access
# Usage: .\aws_garage_config.ps1

$env:AWS_ACCESS_KEY_ID = "GKe8afbd2d80a1ee88b135c9cf"
$env:AWS_SECRET_ACCESS_KEY = "0b432bb89dc684efc3793bb32c6b696e53576c0fff8a03f61077e7e02b01c34b"
$env:AWS_DEFAULT_REGION = "garage"
$env:AWS_ENDPOINT_URL = "http://100.127.76.43:3900"

Write-Host "AWS CLI configured for Garage S3 at $env:AWS_ENDPOINT_URL" -ForegroundColor Green
Write-Host "Key ID: $env:AWS_ACCESS_KEY_ID" -ForegroundColor Yellow
Write-Host "Region: $env:AWS_DEFAULT_REGION" -ForegroundColor Yellow
Write-Host ""
Write-Host "You can now use AWS CLI commands like:" -ForegroundColor Cyan
Write-Host "aws s3 ls" -ForegroundColor White
Write-Host "aws s3 cp file.txt s3://bucket-name/" -ForegroundColor White
Write-Host "aws s3 mb s3://new-bucket" -ForegroundColor White