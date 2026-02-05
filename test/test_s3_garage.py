import boto3
import sys
from botocore.client import Config

# Configuration
GARAGE_IP = "100.127.76.43"
GARAGE_S3_PORT = "3900"
ENDPOINT_URL = f"http://{GARAGE_IP}:{GARAGE_S3_PORT}"

# These must be created via Garage CLI:
# garage key create my_test_key
# garage key list
ACCESS_KEY = "PLACEHOLDER_ACCESS_KEY"
SECRET_KEY = "PLACEHOLDER_SECRET_KEY"

def test_garage_s3():
    print(f"Connecting to Garage S3 at {ENDPOINT_URL}...")
    
    try:
        s3 = boto3.client(
            's3',
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            config=Config(signature_version='s3v4'),
            region_name='garage'
        )

        # 1. List Buckets
        print("\n--- Listing Buckets ---")
        response = s3.list_buckets()
        buckets = [bucket['Name'] for bucket in response['Buckets']]
        print(f"Buckets found: {buckets}")

        # 2. Create a test bucket if it doesn't exist
        test_bucket = "test-garage-bucket"
        if test_bucket not in buckets:
            print(f"\nCreating bucket: {test_bucket}")
            s3.create_bucket(Bucket=test_bucket)
        
        # 3. Upload a small test file
        print("\nUploading test file...")
        s3.put_object(Bucket=test_bucket, Key="hello.txt", Body="Hello from Python and Garage!")
        
        # 4. Download and verify
        print("Downloading test file...")
        obj = s3.get_object(Bucket=test_bucket, Key="hello.txt")
        content = obj['Body'].read().decode('utf-8')
        print(f"Content retrieved: '{content}'")
        
        print("\n✅ Garage S3 Test PASSED!")

    except Exception as e:
        print(f"\n❌ Garage S3 Test FAILED!")
        print(f"Error: {e}")
        print("\nNote: Make sure you have created an access key and bucket permissions in Garage.")
        print("Use: garage key create <name> AND garage bucket create <bucket-name>")
        print("AND garage bucket allow <bucket-name> --key <key-id> --read --write")

if __name__ == "__main__":
    if ACCESS_KEY == "PLACEHOLDER_ACCESS_KEY":
        print("Please update the ACCESS_KEY and SECRET_KEY in the script with values from 'garage key list'")
    test_garage_s3()
