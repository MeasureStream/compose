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

