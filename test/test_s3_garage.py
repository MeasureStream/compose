import boto3
import sys
import socket
import time
from botocore.client import Config

# Configuration - Following Garage Quick Start Guide
GARAGE_HOST = "100.127.76.43"
GARAGE_S3_PORT = 3900
ENDPOINT_URL = f"http://{GARAGE_HOST}:{GARAGE_S3_PORT}"

# These should match your garage key info output
ACCESS_KEY = "GKc9fcbf56ea74a98a1e5913fb"
SECRET_KEY = "ad518281ffd8be8b4417bc722ec171be423feb4c376ef1c406f059fcdc69c40c"

# def check_port():
#     """Check if Garage S3 port is accessible"""
#     print(f"Checking connectivity to {GARAGE_HOST}:{GARAGE_S3_PORT}...")
#     s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
#     s.settimeout(3)
#     try:
#         # Connect to the remote Garage server
#         s.connect((GARAGE_HOST, GARAGE_S3_PORT))
#         s.close()
#         print("✅ Port is OPEN")
#         return True
#     except Exception as e:
#         print(f"❌ Port is CLOSED or UNREACHABLE: {e}")
#         print(f"💡 Make sure Garage is running on {GARAGE_HOST}: 'docker ps | grep garage'")
#         print(f"💡 And that port 3900 is open in the firewall: 'sudo ufw allow 3900/tcp'")
#         return False

def test_garage_s3():
    """Test Garage S3 functionality following the official Quick Start guide"""
    print("=== Garage S3 Quick Start Test ===")

    # if not check_port():
    #     return

    try:
        # Configure S3 client following Garage Quick Start guide
        s3 = boto3.client(
            's3',
            endpoint_url=ENDPOINT_URL,
            aws_access_key_id=ACCESS_KEY,
            aws_secret_access_key=SECRET_KEY,
            region_name='garage',
            config=Config(signature_version='s3v4'),
            verify=False  # Skip SSL verification for local testing
        )

        # 1. List buckets (following garage quick start)
        print("\n--- Listing Buckets ---")
        try:
            response = s3.list_buckets()
            print(response)
            buckets = [bucket['Name'] for bucket in response['Buckets']]
            print(f"✅ Found {len(buckets)} buckets: {buckets}")
        except Exception as e:
            print(f"❌ Failed to list buckets: {e}")
            return

        # 2. Create a bucket (following quick start example)
        test_bucket = "nextcloud-bucket"  # Using name from quick start guide
        if test_bucket not in buckets:
            print(f"\n--- Creating Bucket: {test_bucket} ---")
            try:
                s3.create_bucket(Bucket=test_bucket)
                print(f"✅ Created bucket: {test_bucket}")
            except Exception as e:
                print(f"❌ Failed to create bucket: {e}")
                return

        # 3. Upload a file (following quick start example)
        print(f"\n--- Uploading File to {test_bucket} ---")
        test_file = "cpuinfo.txt"
        test_content = "This is a test file uploaded to Garage S3\n" + str(time.time())
        try:
            s3.put_object(Bucket=test_bucket, Key=test_file, Body=test_content)
            print(f"✅ Uploaded: s3://{test_bucket}/{test_file}")
        except Exception as e:
            print(f"❌ Failed to upload file: {e}")
            return

        # 4. List objects in bucket
        print(f"\n--- Listing Objects in {test_bucket} ---")
        try:
            response = s3.list_objects_v2(Bucket=test_bucket)
            if 'Contents' in response:
                print(f"✅ Found {len(response['Contents'])} objects:")
                for obj in response['Contents']:
                    print(f"   📄 {obj['Key']} ({obj['Size']} bytes, {obj['LastModified']})")
            else:
                print("   📁 Bucket is empty")
        except Exception as e:
            print(f"❌ Failed to list objects: {e}")

        # 5. Download the file
        print(f"\n--- Downloading File ---")
        try:
            obj = s3.get_object(Bucket=test_bucket, Key=test_file)
            downloaded_content = obj['Body'].read().decode('utf-8')
            if downloaded_content == test_content:
                print("✅ Downloaded and verified file content")
            else:
                print("❌ Content mismatch!")
                print(f"Expected: {test_content}")
                print(f"Got: {downloaded_content}")
        except Exception as e:
            print(f"❌ Failed to download file: {e}")

        # 6. Test additional operations
        print(f"\n--- Testing Additional Operations ---")

        # Test copy
        try:
            copy_key = "cpuinfo_copy.txt"
            s3.copy_object(
                CopySource={'Bucket': test_bucket, 'Key': test_file},
                Bucket=test_bucket,
                Key=copy_key
            )
            print("✅ File copy operation successful")
        except Exception as e:
            print(f"❌ File copy failed: {e}")

        # Test delete
        try:
            s3.delete_object(Bucket=test_bucket, Key=copy_key)
            print("✅ File delete operation successful")
        except Exception as e:
            print(f"❌ File delete failed: {e}")

        print("\n🎉 Garage S3 Quick Start Test PASSED!")
        print("Your Garage deployment is working correctly following the official guide.")

    except Exception as e:
        print(f"\n❌ Garage S3 Test FAILED!")
        print(f"Error: {e}")

if __name__ == "__main__":
    test_garage_s3()