#!/bin/bash

# Load environment variables from .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo ".env file not found!"
  exit 1
fi

# Delete the connector if it exists
echo "Deleting connector: $CONNECTOR_NAME..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$CONNECT_URL/$CONNECTOR_NAME")
echo "Connector deleted (HTTP code: $HTTP_CODE)."

# Create connector from JSON file with environment variable substitution
echo "Creating connector from $CONFIG_FILE..."
HTTP_CODE=$(envsubst <"$CONFIG_FILE" | curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data @- "$CONNECT_URL")
echo "Connector created (HTTP code: $HTTP_CODE)."
