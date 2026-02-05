# compose
The final docker compose file to start MeasureStream

## Manual Setup for Garage Service

Before running the stack, you must prepare the external persistent data folders on the host (e.g., Raspberry Pi):

1. **Create Data Folders:**
   ```bash
   mkdir -p /home/mattrovio/garage-data/{data,meta}
   ```

2. **Set Permissions:**
   ```bash
   sudo chown -R $USER:$USER /home/mattrovio/garage-data
   ```

3. **Verify Paths:**
   The `compose-dev.yaml` is currently configured for user `mattrovio`. If your username is different, update the paths in the `volumes` section of the `garage` service.

## Testing Garage S3

A test script is provided in `test/test_s3_garage.py`.

Get your Node ID:
    docker exec -it garage /garage status
Look for the long string under ID (e.g., 0297f928...).
Assign the node to a zone (Replace <NODE_ID> with the ID from step 1):
    docker exec -it garage /garage layout assign <NODE_ID> -z dc1 -c 10G
Apply the layout:
    docker exec -it garage /garage layout apply --version 1
Try creating the key again:

1. **Create S3 Credentials:**
   Inside the Garage container:
   ```bash
   docker exec -it garage /garage key create testkey
   docker exec -it garage /garage key list
   ```

docker exec -it garage /garage key list
==== ACCESS KEY INFORMATION ====
Key ID:              GKe8afbd2d80a1ee88b135c9cf
Key name:            testkey
Secret key:          0b432bb89dc684efc3793bb32c6b696e53576c0fff8a03f61077e7e02b01c34b
Created:             2026-02-05 21:11:28.908 +00:00
Validity:            valid
Expiration:          never

Can create buckets:  false

    docker exec -it garage /garage key allow GKf73ab171533f1f5e902c8d1e --create-bucket
        docker exec -it garage /garage bucket create test-garage-bucket
    docker exec -it garage /garage bucket allow test-garage-bucket --key GKf73ab171533f1f5e902c8d1e --read --write
2. **Give Key Maximum Permissions:**
   ```bash
   docker exec -it garage /garage key allow GKe8afbd2d80a1ee88b135c9cf --create-bucket
   docker exec -it garage /garage key allow GKe8afbd2d80a1ee88b135c9cf --admin
   docker exec -it garage /garage bucket list
   ```

3. **Run Test:**

   #### Option A: Python Test Script
   ```bash
   # Install dependencies if needed
   pip3 install boto3

   # Run the script (connects to 100.127.76.43:3900)
   python test/test_s3_garage.py
   ```

   #### Option B: AWS CLI Configuration
   Use the provided configuration files to access Garage S3 with standard AWS CLI tools.

   **Windows (Command Prompt):**
   ```cmd
   aws_garage_config.bat
   aws s3 ls
   aws s3 cp test.txt s3://nextcloud-bucket/
   ```

   **Windows (PowerShell):**
   ```powershell
   .\aws_garage_config.ps1
   aws s3 ls
   aws s3 cp test.txt s3://nextcloud-bucket/
   ```

   **Linux/macOS (Bash):**
   ```bash
   source aws_garage_config.sh
   aws s3 ls
   aws s3 cp test.txt s3://nextcloud-bucket/
   ```

