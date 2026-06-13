# MeasureStream Compose

## 1. Host Setup (Debian/Raspberry Pi)

Prepare persistent data folders on the host:

```bash
mkdir -p /home/mattrovio/garage-data/{data,meta}
sudo chown -R $USER:$USER /home/mattrovio/garage-data
# sudo ufw allow 3900:3903/tcp
```

## 2. Garage Initialization (One-time)

After `docker compose up -d garage`:

```bash
# 1. Get Node ID
docker exec -it garage /garage status

# 2. Assign Role (replace <ID>)
docker exec -it garage /garage layout assign 47ea164e519f57da -z dc1 -c 10G

# 3. Apply Layout
docker exec -it garage /garage layout apply --version 1
```

## 3. S3 Credentials & Permissions (automatic)

The `garage-init` service bootstraps Garage automatically on first startup:
- Imports the key from `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` env vars (set in `.env`)
- Creates the `dccs` bucket if missing
- Grants read/write permissions

If you need to rotate keys or do manual setup:

```bash
# Create a key manually
docker exec -it garage /garage key create testkey

# Grant permissions (replace <KEY_ID>)
docker exec -it garage /garage key allow <KEY_ID> --create-bucket

# Create & link bucket
docker exec -it garage /garage bucket create dccs
docker exec -it garage /garage bucket allow dccs --key <KEY_ID> --read --write
```

### AWS CLI (Windows)

```cmd
# Command Prompt
aws_garage_config.bat
aws s3 ls

# PowerShell
.\aws_garage_config.ps1
aws s3 ls
```

## 5. Reference Credentials

**Credentials:** set via environment variables `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` (see `.env`).

- **Endpoint:** `http://100.127.76.43:3900`
- **WebUI:** `http://100.127.76.43:3909`

## 6. Admin Utilities

**WebUI Auth (htpasswd):**

```bash
# user: measure / pass: aaaakkkk
measure:$2y$10$27Dez92XK3V5pKKYpV01pOZbz0gyUgZ1WoLOecJRh24qF2JbaMw.O
```
