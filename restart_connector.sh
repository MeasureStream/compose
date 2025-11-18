#!/bin/bash

# Load environment variables from .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo ".env file not found!"
  exit 1
fi

# Define connector name and config file
CONNECTOR_NAME="ttn-mqtt-source"
CONFIG_FILE="kafka-mqtt-source.json"
CONNECT_URL="http://localhost:8083/connectors"

# Delete the connector if it exists
echo "Deleting connector: $CONNECTOR_NAME..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE "$CONNECT_URL/$CONNECTOR_NAME")
echo "Connector deleted (HTTP code: $HTTP_CODE)."

# Create connector from JSON file with environment variable substitution
echo "Preparing connector configuration..."
CONFIG_CONTENT=$(envsubst <"$CONFIG_FILE")

echo -e "\n==== Connector Configuration ===="
echo "$CONFIG_CONTENT"
echo "=================================\n"

echo "Creating connector from config..."
HTTP_CODE=$(echo "$CONFIG_CONTENT" | curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data @- "$CONNECT_URL")
echo "Connector created (HTTP code: $HTTP_CODE)."
