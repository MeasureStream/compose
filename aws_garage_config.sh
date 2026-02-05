# AWS CLI Configuration for Garage S3
# Source this file to configure AWS CLI for Garage S3 access
# Usage: source aws_garage_config.sh

export AWS_ACCESS_KEY_ID=GKe8afbd2d80a1ee88b135c9cf
export AWS_SECRET_ACCESS_KEY=0b432bb89dc684efc3793bb32c6b696e53576c0fff8a03f61077e7e02b01c34b
export AWS_DEFAULT_REGION='garage'
export AWS_ENDPOINT_URL='http://100.127.76.43:3900'

echo "AWS CLI configured for Garage S3 at $AWS_ENDPOINT_URL"
echo "Key ID: $AWS_ACCESS_KEY_ID"
echo "Region: $AWS_DEFAULT_REGION"