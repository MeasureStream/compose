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
docker exec -it garage /garage layout assign <ID> -z dc1 -c 10G

# 3. Apply Layout
docker exec -it garage /garage layout apply --version 1
```

## 3. S3 Credentials & Permissions

```bash
# 1. Create Key
docker exec -it garage /garage key create testkey

# 2. Grant Full Permissions (replace <KEY_ID>)
docker exec -it garage /garage key allow <KEY_ID> --create-bucket

# 3. Create & Link Bucket
docker exec -it garage /garage bucket create nextcloud-bucket
docker exec -it garage /garage bucket allow nextcloud-bucket --key <KEY_ID> --read --write
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

**Current Test Key:**

- **Key ID:** `GKc9fcbf56ea74a98a1e5913fb`
- **Secret:** `ad518281ffd8be8b4417bc722ec171be423feb4c376ef1c406f059fcdc69c40c`

- **Endpoint:** `http://100.127.76.43:3900`
- **Endpoint:** `http://100.127.76.43:3909`

## 6. Admin Utilities

**WebUI Auth (htpasswd):**

```bash
# user: measure / pass: aaaakkkk
measure:$2y$10$27Dez92XK3V5pKKYpV01pOZbz0gyUgZ1WoLOecJRh24qF2JbaMw.O
```
