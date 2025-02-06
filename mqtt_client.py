# mqtt_client.py
import asyncio
import json
import uuid
from aiomqtt import Client as MQTTClient
from config import settings
from db import database

# Temperature threshold in °C
TEMPERATURE_THRESHOLD = 70

async def process_message(message):
    try:
        # Decode and parse the MQTT payload.
        payload_str = message.payload.decode("utf-8")
        data = json.loads(payload_str)
        datastream_id = data["datastream_id"]  # should be a valid UUID string
        result = data["result"]  # e.g., {"value": 21.7, "unit": "°C"}
        temperature_value = float(result.get("value", 0))

        # Prepare the INSERT query using named parameters.
        # We pass Python UUID objects so that the driver infers the type correctly.
        insert_query = """
            INSERT INTO observation (id, datastream_id, phenomenon_time, result, created_at)
            VALUES (:obs_id, :ds_id, NOW(), CAST(:result_text AS jsonb), NOW())
        """
        insert_values = {
            "obs_id": uuid.uuid4(),                 # Pass as a Python UUID object
            "ds_id": uuid.UUID(datastream_id),        # Convert the datastream_id string to a UUID object
            "result_text": json.dumps(result)         # JSON string for the result
        }
        await database.execute(query=insert_query, values=insert_values)
        print(f"Inserted observation for datastream {datastream_id}")

        # Fetch the corresponding zone.
        # We assume the datastream is linked to a zone via the feature_of_interest.
        zone_query = """
            SELECT z.id, z.name 
            FROM zone z
            JOIN datastream d ON d.feature_of_interest_id = z.feature_of_interest_id
            WHERE d.id = :ds_id
            LIMIT 1
        """
        zone = await database.fetch_one(query=zone_query, values={"ds_id": uuid.UUID(datastream_id)})
        
        if zone:
            if temperature_value > TEMPERATURE_THRESHOLD:
    # Temperature exceeds threshold: update the zone with an alert.
                update_query = """
                    UPDATE zone
                    SET properties = COALESCE(properties, '{}'::jsonb)
                        || jsonb_build_object('current_temp', CAST(:temp AS numeric), 'alert', 'Temperature exceeds threshold')
                    WHERE id = CAST(:zone_id AS uuid)
                """
                print(f"ALERT: {temperature_value}°C in zone {zone['name']}!")
            else:
            # Temperature is normal: update the zone's current temperature and remove any alert.
                update_query = """
                    UPDATE zone
                    SET properties = (COALESCE(properties, '{}'::jsonb)
                        || jsonb_build_object('current_temp', CAST(:temp AS numeric))) - 'alert'
                    WHERE id = CAST(:zone_id AS uuid)
                """
                update_values = {
                "temp": temperature_value,
                "zone_id": str(zone["id"])  # ensure it's a string (or use uuid.UUID if preferred)
                }
            await database.execute(query=update_query, values=update_values)
            print(f"Updated zone '{zone['name']}' with temperature {temperature_value}°C.")

            print(f"Updated zone '{zone['name']}' with temperature {temperature_value}°C.")
        else:
            print(f"Warning: No zone found for datastream {datastream_id}")

    except Exception as e:
        print(f"Error processing MQTT message: {e}")

async def mqtt_listener():
    async with MQTTClient(settings.MQTT_BROKER, settings.MQTT_PORT) as client:
        await client.subscribe("iot_safeindustech/sensors/#")
        async for message in client.messages:
            await process_message(message)

def start_mqtt_listener():
    loop = asyncio.get_event_loop()
    loop.create_task(mqtt_listener())
