"""
Invia i due messaggi di calibrazione dal file one_measure.json
al topic "calibrations" uno alla volta.

Uso:
    python send_calibration_messages.py           # invia entrambi
    python send_calibration_messages.py --msg 0   # solo step 0
    python send_calibration_messages.py --msg 1   # solo step 1

Kafka bootstrap: 100.78.181.75:9092 (Tailscale esterno)
"""

import json
import sys
import os
import argparse
from kafka import KafkaProducer

# BOOTSTRAP = "100.78.181.75:9092"
BOOTSTRAP = "100.87.231.127:9092"

TOPIC = "calibrations"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
MSG_FILE = os.path.join(SCRIPT_DIR, "one_measure.json")


def send(producer, msg, index):
    print(f"\n=== Sending message {index} ===")
    print(f"  calib_id   : {msg['calib_id']}")
    print(f"  step_index : {msg['step_index']}")
    print(f"  target     : {msg['target']} °C")
    print(f"  steps total: {len(msg.get('step_summary', []))}")

    payload = json.dumps(msg).encode("utf-8")
    future = producer.send(TOPIC, value=payload)
    meta = future.get(timeout=15)
    print(f"  Sent OK -> partition={meta.partition} offset={meta.offset}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--msg", type=int, choices=[0, 1], default=None,
                        help="Which message to send (0 or 1). Default: both.")
    args = parser.parse_args()

    print(f"Loading messages from {MSG_FILE}...")
    with open(MSG_FILE, encoding="utf-8") as f:
        messages = json.load(f)

    print(f"Found {len(messages)} messages (steps) in file.")

    print(f"\nConnecting to Kafka at {BOOTSTRAP}...")
    producer = KafkaProducer(
        bootstrap_servers=[BOOTSTRAP],
        request_timeout_ms=15000,
        retries=3,
    )

    try:
        if args.msg is not None:
            send(producer, messages[args.msg], args.msg)
        else:
            for i, msg in enumerate(messages):
                send(producer, msg, i)

        producer.flush()
        print("\nDone. Messages sent successfully.")
    except Exception as e:
        print(f"\nERROR: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        producer.close()


if __name__ == "__main__":
    main()

