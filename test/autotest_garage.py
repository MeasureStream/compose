import os
import subprocess
import json
import time
import boto3
from botocore.client import Config

# Configuration
GARAGE_IP = "100.127.76.43"
GARAGE_S3_PORT = "3900"
ENDPOINT_URL = f"http://{GARAGE_IP}:{GARAGE_S3_PORT}"
BUCKET_NAME = "test-garage-bucket"

def run_command(cmd):
    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: {result.stderr}")
        return None
    return result.stdout.strip()

def get_garage_keys():
    # Try to list keys
    output = run_command(["docker", "exec", "garage", "/garage", "key", "list"])
    if not output:
        # Try to create one if none exist or command failed
        print("No keys found, creating 'testkey'...")
        run_command(["docker", "exec", "garage", "/garage", "key", "create", "testkey"])
        output = run_command(["docker", "exec", "garage", "/garage", "key", "list"])
    
    if not output:
        return None, None

    # Parse output to find keys
    # Garage key list output format is usually a table
    lines = output.splitlines()
    for line in lines:
        if "GK" in line: # Typical Garage access key prefix
            parts = line.split()
            # This is a bit fragile, but usually: [ID, Name, AccessKey, SecretKey]
            # Let's try to find the one that looks like keys
            for part in parts:
                if part.startswith("GK"):
                    access_key = part
                    # The secret is usually the next or previous long string
                    # But actually, 'key list' shows the ID and Name.
                    # To get the secret, we might need 'key info <id>'
                    key_id = parts[0]
                    info = run_command(["docker", "exec", "garage", "/garage", "key", "info", key_id])
                    if info:
                        for info_line in info.splitlines():
                            if "Access key ID:" in info_line:
                                access_key = info_line.split(":")[-1].strip()
                            if "Secret access key:" in info_line:
                                secret_key = info_line.split(":")[-1].strip()
                        return access_key, secret_key
    return None, None

def setup_bucket(access_key):
    print(f"Ensuring bucket '{BUCKET_NAME}' exists and key has permissions...")
    run_command(["docker", "exec", "garage", "/garage", "bucket", "create", BUCKET_NAME])
    run_command(["docker", "exec", "garage", "/garage", "bucket", "allow", BUCKET_NAME, "--key", access_key, "--read", "--write"])

def test_s3(access_key, secret_key):
    print(f"\nTesting S3 connection to {ENDPOINT_URL}...")
    try:
        s3 = boto3.client(
            's3',
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            config=Config(signature_version='s3v4'),
            region_name='garage'
        )

        print("Listing buckets...")
        response = s3.list_buckets()
        print(f"Buckets: {[b['Name'] for b in response['Buckets']]}")

        print(f"Uploading to {BUCKET_NAME}...")
        s3.put_object(Bucket=BUCKET_NAME, Key="test.txt", Body="Garage is working!")
        
        print("Downloading and verifying...")
        obj = s3.get_object(Bucket=BUCKET_NAME, Key="test.txt")
        print(f"Content: {obj['Body'].read().decode('utf-8')}")
        
        print("\n✅ SUCCESS: Garage S3 is fully operational!")
        return True
    except Exception as e:
        print(f"\n❌ S3 Test Failed: {e}")
        return False

def main():
    print("--- Garage Automated Test & Setup ---")
    
    # 1. Get keys from docker
    access_key, secret_key = get_garage_keys()
    if not access_key or not secret_key:
        print("Could not retrieve Garage keys. Are you running this on the machine with the 'garage' container?")
        return

    print(f"Using Access Key: {access_key}")
    
    # 2. Setup bucket
    setup_bucket(access_key)
    
    # 3. Test
    test_s3(access_key, secret_key)

if __name__ == "__main__":
    main()
