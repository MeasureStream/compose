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

mattrovio@measurestream-dev:~/compose$ docker exec -it garage /garage key create testkey
docker exec -it garage /garage key list
2026-02-05T20:44:48.789159Z  INFO garage_net::netapp: Connected to 172.20.0.60:3901, negotiating handshake...
2026-02-05T20:44:48.832173Z  INFO garage_net::netapp: Connection established to 0297f928e9948c1a
==== ACCESS KEY INFORMATION ====
Key ID:              GKf73ab171533f1f5e902c8d1e
Key name:            testkey
Secret key:          c41c033a237b9732854328fcc533fd15b67ca23cbd51142c77dc2b7c32599137
Created:             2026-02-05 20:44:48.832 +00:00
Validity:            valid
Expiration:          never

Can create buckets:  false

==== BUCKETS FOR THIS KEY ====
Permissions  ID  Global aliases  Local aliases
2026-02-05T20:44:48.910280Z  INFO garage_net::netapp: Connected to 172.20.0.60:3901, negotiating handshake...
2026-02-05T20:44:48.952167Z  INFO garage_net::netapp: Connection established to 0297f928e9948c1a
ID                          Created     Name     Expiration
GKf73ab171533f1f5e902c8d1e  2026-02-05  testkey  never
mattrovio@measurestream-dev:~/compose$

    docker exec -it garage /garage key allow GKf73ab171533f1f5e902c8d1e --create-bucket
        docker exec -it garage /garage bucket create test-garage-bucket
    docker exec -it garage /garage bucket allow test-garage-bucket --key GKf73ab171533f1f5e902c8d1e --read --write
2. **Run Test:**
   Install dependencies: `pip install boto3`
   Update the placeholders in `test/test_s3_garage.py` and run:
   ```bash
   python test/test_s3_garage.py
   ```

