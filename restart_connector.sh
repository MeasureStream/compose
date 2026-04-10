#!/bin/bash

# 1. Carica variabili d'ambiente
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
else
  echo ".env file not found!"
  exit 1
fi

CONTAINER_NAME="kafka-connect"
CONNECT_URL="http://localhost:8083/connectors"

# 2. Funzione per attendere che l'API sia pronta
echo "Attesa che Kafka Connect sia pronto..."
until docker exec $CONTAINER_NAME curl -s -f http://localhost:8083/ >/dev/null; do
  printf '.'
  sleep 2
done
echo -e "\nKafka Connect è ONLINE!"

# --- CONFIGURAZIONE SOURCE ---
CONNECTOR_SOURCE="ttn-mqtt-source"
FILE_SOURCE="kafka-mqtt-source.json"

echo "Eliminazione vecchio Source: $CONNECTOR_SOURCE..."
docker exec $CONTAINER_NAME curl -s -X DELETE "$CONNECT_URL/$CONNECTOR_SOURCE"

echo "Creazione nuovo Source..."
CONFIG_SOURCE=$(envsubst '${MQTT_BROKER} ${MQTT_USERNAME} ${MQTT_PASSWORD}' <"$FILE_SOURCE")
echo "$CONFIG_SOURCE" | docker exec -i $CONTAINER_NAME curl -s -X POST -H "Content-Type: application/json" --data @- "$CONNECT_URL"

# --- CONFIGURAZIONE SINK ---
CONNECTOR_SINK="ttn-mqtt-sink"
FILE_SINK="kafka-mqtt-sink.json"

echo "Eliminazione vecchio Sink: $CONNECTOR_SINK..."
docker exec $CONTAINER_NAME curl -s -X DELETE "$CONNECT_URL/$CONNECTOR_SINK"

echo "Creazione nuovo Sink..."
CONFIG_SINK=$(envsubst '${MQTT_BROKER} ${MQTT_USERNAME} ${MQTT_PASSWORD}' <"$FILE_SINK")
echo "$CONFIG_SINK" | docker exec -i $CONTAINER_NAME curl -s -X POST -H "Content-Type: application/json" --data @- "$CONNECT_URL"

echo -e "\nOperazione completata. Verifica stati:"
docker exec $CONTAINER_NAME curl -s "$CONNECT_URL?expand=status" | grep -E "name|state"
