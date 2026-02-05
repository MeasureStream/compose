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

1. **Create S3 Credentials:**
   Inside the Garage container:
   ```bash
   docker exec -it garage /garage key create testkey
   docker exec -it garage /garage key list
   ```

2. **Run Test:**
   Install dependencies: `pip install boto3`
   Update the placeholders in `test/test_s3_garage.py` and run:
   ```bash
   python test/test_s3_garage.py
   ```

