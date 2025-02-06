# main.py
import asyncio
import json
import uuid
import uvicorn
from fastapi import FastAPI, WebSocket, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from db import database
from mqtt_client import start_mqtt_listener
from config import settings
from fastapi_utils.tasks import repeat_every
from sensors_router import sensors_router
from observed_properties_router import observed_properties_router
from observations_router import observations_router
from alerts_router import alerts_router
from fastapi import HTTPException


app = FastAPI()

# Allow CORS for development
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to specific domains for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(sensors_router)
app.include_router(observed_properties_router)
app.include_router(observations_router)
app.include_router(alerts_router)


# Startup and Shutdown events

@app.get("/usine")
async def get_usine():
    query = "SELECT * FROM usine LIMIT 1"
    usine = await database.fetch_one(query=query)
    if usine:
        return usine
    raise HTTPException(status_code=404, detail="Usine not found")

@app.on_event("startup")
async def startup_event():
    await database.connect()
    start_mqtt_listener()
    print("Database connected and MQTT listener started.")

@app.on_event("shutdown")
async def shutdown_event():
    await database.disconnect()
    print("Database disconnected.")

# REST endpoint: Get the latest observation for a given datastream ID.
@app.get("/observations/{datastream_id}")
async def get_latest_observation(datastream_id: str):
    query = """
        SELECT result, phenomenon_time
        FROM observation
        WHERE datastream_id = :datastream_id
        ORDER BY phenomenon_time DESC
        LIMIT 1
    """
    row = await database.fetch_one(query=query, values={"datastream_id": datastream_id})
    if row:
        return {
            "datastream_id": datastream_id,
            "result": json.loads(row["result"]),
            "timestamp": row["phenomenon_time"].isoformat()
        }
    return {"message": "No observation found."}

# REST endpoint: Get zone status (e.g., alerts) for all zones.


@app.get("/zones/status")
async def get_zone_status():
    query = """
        SELECT z.name AS zone_name, z.risk_level, z.properties 
        FROM zone z
    """
    
    rows = await database.fetch_all(query=query)
    zones = []
    for row in rows:
        properties = json.loads(row["properties"]) if row["properties"] else {}

        zones.append({
            "name": row["zone_name"],
            "risk_level": row["risk_level"],
            "temperature": properties.get("current_temp", "N/A"),
            "alert": properties.get("alert", None)  # Show alert if exists
        })

    return zones

# WebSocket endpoint: Stream the latest observation for a given datastream.
@app.websocket("/ws/{datastream_id}")
async def websocket_endpoint(websocket: WebSocket, datastream_id: str):
    await websocket.accept()
    while True:
        query = """
            SELECT result, phenomenon_time
            FROM observation
            WHERE datastream_id = :datastream_id
            ORDER BY phenomenon_time DESC
            LIMIT 1
        """
        row = await database.fetch_one(query=query, values={"datastream_id": datastream_id})
        if row:
            await websocket.send_json({
                "datastream_id": datastream_id,
                "result": json.loads(row["result"]),
                "timestamp": row["phenomenon_time"].isoformat()
            })
        await asyncio.sleep(1)

# Background task: Periodically check the latest temperature and trigger alerts if needed.
@app.on_event("startup")
@repeat_every(seconds=10)  # Check every 10 seconds.
async def check_temperature_threshold():
    query = """
        SELECT result, phenomenon_time
        FROM observation
        WHERE datastream_id = :datastream_id
        ORDER BY phenomenon_time DESC
        LIMIT 1
    """
    row = await database.fetch_one(query=query, values={"datastream_id": settings.TEMP_DATASTREAM_ID})
    if row:
        result = json.loads(row["result"])
        temperature_value = result.get("value")
        if temperature_value and float(temperature_value) > 70:
            print(f"Background Task ALERT: Temperature {temperature_value}°C exceeds threshold!")
            await mqtt_client.trigger_temperature_alert(settings.TEMP_DATASTREAM_ID, float(temperature_value))

# SensorThings API Router (for demonstration, we add a simple Things endpoint)
things_router = APIRouter(prefix="/Things", tags=["Things"])

@things_router.get("/")
async def get_things():
    query = "SELECT * FROM thing"
    return await database.fetch_all(query=query)

app.include_router(things_router)

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
