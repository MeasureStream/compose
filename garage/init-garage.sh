#!/bin/sh
# Garage S3 bootstrap: idempotently creates key + bucket + permissions
set -e

ADMIN_API="http://garage:3903"
ADMIN_TOKEN="${GARAGE_ADMIN_TOKEN}"
KEY_NAME="dcc-service"
KEY_ID="${AWS_ACCESS_KEY_ID}"
KEY_SECRET="${AWS_SECRET_ACCESS_KEY}"
BUCKET="dccs"
BUCKET_REGION="garage"
MAX_RETRIES=30

auth_header() {
    echo "Authorization: Bearer ${ADMIN_TOKEN}"
}

# Wait for Garage admin API
echo "[garage-init] Waiting for Garage admin API at ${ADMIN_API}..."
i=0
while [ $i -lt $MAX_RETRIES ]; do
    if curl -sf "${ADMIN_API}/health" > /dev/null 2>&1; then
        echo "[garage-init] Garage is ready."
        break
    fi
    i=$((i + 1))
    echo "[garage-init] Waiting... ($i/$MAX_RETRIES)"
    sleep 2
done
if [ $i -ge $MAX_RETRIES ]; then
    echo "[garage-init] ERROR: Garage did not become ready in time."
    exit 1
fi

# Check if key already exists by searching for the configured key ID
if [ -n "$KEY_ID" ]; then
    echo "[garage-init] Checking for existing key ${KEY_ID}..."
    SEARCH_RESULT=$(curl -sf -H "$(auth_header)" "${ADMIN_API}/key?search=${KEY_ID}&showSecretKey=true" 2>/dev/null || echo "")
    if echo "$SEARCH_RESULT" | grep -q "\"${KEY_ID}\""; then
        echo "[garage-init] Key ${KEY_ID} already exists, skipping key creation."
        KEY_EXISTS=1
    fi
fi

# If key doesn't exist, import from env vars (backup restore)
if [ -z "$KEY_EXISTS" ] && [ -n "$KEY_ID" ] && [ -n "$KEY_SECRET" ]; then
    echo "[garage-init] Importing key ${KEY_ID} from environment..."
    IMPORT_RESULT=$(curl -sf -X POST "${ADMIN_API}/key/import" \
        -H "$(auth_header)" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${KEY_NAME}\",\"accessKeyId\":\"${KEY_ID}\",\"secretAccessKey\":\"${KEY_SECRET}\"}" 2>/dev/null || echo "")
    if echo "$IMPORT_RESULT" | grep -q "\"accessKeyId\""; then
        echo "[garage-init] Key imported successfully."
        KEY_EXISTS=1
    else
        echo "[garage-init] Key import failed (response: ${IMPORT_RESULT})"
    fi
fi

# If still no key, create a new one
if [ -z "$KEY_EXISTS" ]; then
    echo "[garage-init] Creating new key '${KEY_NAME}'..."
    CREATE_RESULT=$(curl -sf -X POST "${ADMIN_API}/key" \
        -H "$(auth_header)" \
        -H "Content-Type: application/json" \
        -d "{\"name\":\"${KEY_NAME}\"}" 2>/dev/null || echo "")
    if echo "$CREATE_RESULT" | grep -q "\"accessKeyId\""; then
        echo "[garage-init] New key created:"
        echo "$CREATE_RESULT"
        # Extract key ID for bucket allow step
        KEY_ID=$(echo "$CREATE_RESULT" | sed -n 's/.*"accessKeyId"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        echo ""
        echo "=============================================="
        echo " UPDATE your .env file with these credentials:"
        echo "=============================================="
        echo "AWS_ACCESS_KEY_ID=${KEY_ID}"
        KEY_SECRET=$(echo "$CREATE_RESULT" | sed -n 's/.*"secretAccessKey"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
        echo "AWS_SECRET_ACCESS_KEY=${KEY_SECRET}"
        echo "=============================================="
        echo ""
    else
        echo "[garage-init] ERROR: Failed to create key (response: ${CREATE_RESULT})"
        exit 1
    fi
fi

# Create bucket if it doesn't exist
echo "[garage-init] Checking for bucket '${BUCKET}'..."
BUCKETS=$(curl -sf -H "$(auth_header)" "${ADMIN_API}/bucket?list" 2>/dev/null || echo "[]")
if echo "$BUCKETS" | grep -q "\"${BUCKET}\""; then
    echo "[garage-init] Bucket '${BUCKET}' already exists."
    # Extract bucket ID
    BUCKET_ID=$(echo "$BUCKETS" | sed -n "/\"${BUCKET}\"/{s/.*\"id\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p;q}")
else
    echo "[garage-init] Creating bucket '${BUCKET}'..."
    CREATE_BUCKET=$(curl -sf -X POST "${ADMIN_API}/bucket" \
        -H "$(auth_header)" \
        -H "Content-Type: application/json" \
        -d "{\"globalAlias\":\"${BUCKET}\"}" 2>/dev/null || echo "")
    BUCKET_ID=$(echo "$CREATE_BUCKET" | sed -n 's/.*"id"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p')
    echo "[garage-init] Bucket created: ${BUCKET_ID}"
fi

# Grant key permissions on bucket
if [ -n "$KEY_ID" ] && [ -n "$BUCKET_ID" ]; then
    echo "[garage-init] Granting permissions (read+write) on '${BUCKET}' to key '${KEY_ID}'..."
    curl -sf -X POST "${ADMIN_API}/bucket/allow" \
        -H "$(auth_header)" \
        -H "Content-Type: application/json" \
        -d "{\"bucketId\":\"${BUCKET_ID}\",\"accessKeyId\":\"${KEY_ID}\",\"permissions\":{\"read\":true,\"write\":true,\"owner\":false}}" \
        > /dev/null 2>&1 && echo "[garage-init] Permissions granted." || echo "[garage-init] WARNING: Failed to grant permissions."
fi

echo "[garage-init] Bootstrap complete."
