import boto3
import sys
import socket
from botocore.client import Config

# Configuration
GARAGE_HOST = "localhost"
GARAGE_S3_PORT = 3900
ENDPOINT_URL = f"http://{GARAGE_HOST}:{GARAGE_S3_PORT}"

ACCESS_KEY = "GKf73ab171533f1f5e902c8d1e"
SECRET_KEY = "c41c033a237b9732854328fcc533fd15b67ca23cbd51142c77dc2b7c32599137"

def check_port():
    print(f"Checking connectivity to {GARAGE_HOST}:{GARAGE_S3_PORT}...")
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(3)
    try:
        # Resolve localhost to IPv4
        s.connect(('127.0.0.1', GARAGE_S3_PORT))
        s.close()
        print("✅ Port is OPEN")
        return True
    except Exception as e:
        print(f"❌ Port is CLOSED or UNREACHABLE: {e}")
        return False

def test_garage_s3():
    if not check_port():
        print("\nPossible fixes:")
        print("1. Ensure the container is running: 'docker ps | grep garage'")
        print("2. Check container logs: 'docker logs garage'")
        return
    
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

if __name__ == "__main__":
    test_garage_s3()
