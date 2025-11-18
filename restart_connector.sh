#!/bin/bash

# Define connector name and config file
CONNECTOR_NAME="ttn-mqtt-source"
CONFIG_FILE="mqtt-source.json"
CONNECT_URL="http://localhost:8083/connectors"

# Delete the connector if it exists
echo "Deleting connector: $CONNECTOR_NAME..."
curl -s -o /dev/null -w "%{http_code}" -X DELETE "$CONNECT_URL/$CONNECTOR_NAME"
echo "Connector deleted (if it existed)."

# Create connector from JSON file
echo "Creating connector from $CONFIG_FILE..."
curl -s -o /dev/null -w "%{http_code}" -X POST -H "Content-Type: application/json" --data @"$CONFIG_FILE" "$CONNECT_URL"
echo "Connector created."
