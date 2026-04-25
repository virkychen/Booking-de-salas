import os
from datetime import datetime

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

SCHEDULE_SERVICE_URL = os.getenv("SCHEDULE_SERVICE_URL", "http://localhost:5002")

AVAILABLE_ROOMS = {"A101", "A102", "B201", "B202"}


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok", "service": "room-service"}, 200


@app.post("/rooms/check-and-book")
def check_and_book():
    payload = request.get_json(silent=True) or {}
    room_id = payload.get("room_id")
    date = payload.get("date")
    start_time = payload.get("start_time")
    duration_hours = payload.get("duration_hours", 1)
    user_id = payload.get("user_id", "anonymous")

    if room_id not in AVAILABLE_ROOMS:
        return jsonify({"status": "rejected", "message": "Sala inexistente"}), 404

    if not date or not start_time:
        return jsonify({"status": "rejected", "message": "date/start_time obrigatorios"}), 400

    try:
        duration_hours = int(duration_hours)
    except (TypeError, ValueError):
        return jsonify({"status": "rejected", "message": "duration_hours invalido"}), 400
    if duration_hours < 1 or duration_hours > 5:
        return jsonify({"status": "rejected", "message": "duration_hours deve ser 1 a 5"}), 400

    check_resp = requests.get(
        f"{SCHEDULE_SERVICE_URL}/schedule/availability",
        params={
            "room_id": room_id,
            "date": date,
            "start_time": start_time,
            "duration_hours": duration_hours,
        },
        timeout=2,
    )
    check_resp.raise_for_status()
    available_payload = check_resp.json()

    if not available_payload.get("available", False):
        return jsonify({"status": "rejected", "message": "Horario indisponivel"}), 409

    register_resp = requests.post(
        f"{SCHEDULE_SERVICE_URL}/schedule/bookings",
        json={
            "room_id": room_id,
            "date": date,
            "start_time": start_time,
            "duration_hours": duration_hours,
            "user_id": user_id,
            "created_at": datetime.utcnow().isoformat() + "Z",
        },
        timeout=2,
    )
    register_resp.raise_for_status()

    return (
        jsonify(
            {
                "status": "confirmed",
                "message": "Sala reservada com sucesso",
                "schedule_response": register_resp.json(),
            }
        ),
        201,
    )


@app.get("/rooms/availability")
def rooms_availability():
    """Disponibilidade de um intervalo (start_time + duration_hours) para todas as salas."""
    date = request.args.get("date")
    start_time = request.args.get("start_time")
    duration_raw = request.args.get("duration_hours", "1")

    if not date or not start_time:
        return jsonify({"error": "date/start_time obrigatorios"}), 400
    try:
        duration_hours = int(duration_raw)
    except ValueError:
        return jsonify({"error": "duration_hours invalido"}), 400

    results = []
    for rid in sorted(AVAILABLE_ROOMS):
        check_resp = requests.get(
            f"{SCHEDULE_SERVICE_URL}/schedule/availability",
            params={
                "room_id": rid,
                "date": date,
                "start_time": start_time,
                "duration_hours": duration_hours,
            },
            timeout=2,
        )
        check_resp.raise_for_status()
        available_payload = check_resp.json()
        results.append(
            {"room_id": rid, "available": available_payload.get("available", False)}
        )

    return (
        jsonify(
            {
                "date": date,
                "start_time": start_time,
                "duration_hours": duration_hours,
                "rooms": results,
            }
        ),
        200,
    )


@app.get("/rooms/availability-matrix")
def rooms_availability_matrix():
    """Matriz hora a hora (uma consulta ao Schedule)."""
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date obrigatorio"}), 400

    resp = requests.get(
        f"{SCHEDULE_SERVICE_URL}/schedule/hourly-matrix",
        params={"date": date},
        timeout=10,
    )
    resp.raise_for_status()
    return jsonify(resp.json()), 200


@app.get("/rooms/bookings")
def list_bookings():
    resp = requests.get(f"{SCHEDULE_SERVICE_URL}/schedule/bookings", timeout=5)
    resp.raise_for_status()
    return jsonify(resp.json()), 200


@app.delete("/rooms/bookings/<booking_id>")
def cancel_booking(booking_id: str):
    resp = requests.delete(f"{SCHEDULE_SERVICE_URL}/schedule/bookings/{booking_id}", timeout=5)
    if resp.status_code >= 400:
        return jsonify(resp.json()), resp.status_code
    return jsonify(resp.json()), 200


@app.get("/rooms/events")
def list_events():
    limit = request.args.get("limit", "100")
    resp = requests.get(f"{SCHEDULE_SERVICE_URL}/schedule/events", params={"limit": limit}, timeout=5)
    resp.raise_for_status()
    return jsonify(resp.json()), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=False)
