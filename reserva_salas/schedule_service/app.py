import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request

app = Flask(__name__)

BOOKINGS_PATH = Path(os.getenv("BOOKINGS_PATH", "/data/bookings.json"))
EVENTS_PATH = Path(os.getenv("EVENTS_PATH", "/data/events.jsonl"))
PENDING_PATH = Path(os.getenv("PENDING_PATH", "/data/pending.json"))
OPERATING_START_HOUR = int(os.getenv("OPERATING_START_HOUR", "9"))
OPERATING_END_HOUR = int(os.getenv("OPERATING_END_HOUR", "23"))
# Janela [OPERATING_START_HOUR, OPERATING_END_HOUR): inicio da reserva >= inicio;
# fim (inicio + duracao) nao pode ultrapassar OPERATING_END_HOUR (ex.: 9h as 23h).

_lock = threading.Lock()
_bookings: list[dict[str, Any]] = []
_events: list[dict[str, Any]] = []
_pending: list[dict[str, Any]] = []


def _hour_from_start_time(start_time: str) -> int:
    parts = (start_time or "").strip().split(":")
    if len(parts) < 2:
        raise ValueError("start_time invalido")
    h = int(parts[0])
    m = int(parts[1])
    if m != 0 or h < 0 or h > 23:
        raise ValueError("Use hora cheia no formato HH:00")
    return h


def _hours_covered(start_hour: int, duration_hours: int) -> set[int]:
    return set(range(start_hour, start_hour + duration_hours))


def _load_disk() -> list[dict[str, Any]]:
    if not BOOKINGS_PATH.exists():
        return []
    try:
        raw = BOOKINGS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and "bookings" in data:
            return list(data["bookings"])
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_disk() -> None:
    BOOKINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = BOOKINGS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_bookings, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(BOOKINGS_PATH)


def _load_pending() -> list[dict[str, Any]]:
    if not PENDING_PATH.exists():
        return []
    try:
        raw = PENDING_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return []


def _save_pending() -> None:
    PENDING_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = PENDING_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(_pending, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PENDING_PATH)


def _append_event(event: dict[str, Any]) -> None:
    EVENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(event, ensure_ascii=False) + "\n"
    with EVENTS_PATH.open("a", encoding="utf-8") as f:
        f.write(line)
    _events.append(event)


def _load_events(limit: int = 200) -> list[dict[str, Any]]:
    if not EVENTS_PATH.exists():
        return []
    events: list[dict[str, Any]] = []
    try:
        with EVENTS_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return events[-limit:]


def _occupied_hours(room_id: str, date: str) -> set[int]:
    occ: set[int] = set()
    for b in _bookings:
        if b.get("room_id") != room_id or b.get("date") != date:
            continue
        sh = int(b["start_hour"])
        dur = int(b["duration_hours"])
        occ |= _hours_covered(sh, dur)
    return occ


def _range_available(room_id: str, date: str, start_hour: int, duration_hours: int) -> bool:
    need = _hours_covered(start_hour, duration_hours)
    if not need:
        return False
    if start_hour < OPERATING_START_HOUR:
        return False
    if start_hour + duration_hours > OPERATING_END_HOUR:
        return False
    occ = _occupied_hours(room_id, date)
    return need.isdisjoint(occ)


def _hour_free(room_id: str, date: str, hour: int) -> bool:
    return hour not in _occupied_hours(room_id, date)


def _ranges_overlap(start_a: int, dur_a: int, start_b: int, dur_b: int) -> bool:
    end_a = start_a + dur_a
    end_b = start_b + dur_b
    return start_a < end_b and start_b < end_a


def _cancel_conflicting_pending(room_id: str, date: str, start_hour: int, duration_hours: int) -> bool:
    changed = False
    for p in _pending:
        if p.get("status") != "waiting":
            continue
        if p.get("room_id") != room_id or p.get("date") != date:
            continue
        p_start = int(p.get("start_hour", -1))
        p_dur = int(p.get("duration_hours", 0))
        if _ranges_overlap(start_hour, duration_hours, p_start, p_dur):
            p["status"] = "cancelled_conflict"
            p["cancel_reason"] = "Conflito: horario foi reservado por outro pedido."
            p["cancelled_at_ts"] = int(time.time())
            changed = True
            _append_event(
                {
                    "ts": int(time.time()),
                    "type": "pending_cancelled_conflict",
                    "pending_id": p.get("pending_id"),
                    "room_id": room_id,
                    "date": date,
                }
            )
    return changed


with _lock:
    _bookings = _load_disk()
    _events = _load_events()
    _pending = _load_pending()


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok", "service": "schedule-service"}, 200


@app.get("/schedule/availability")
def availability():
    """Verifica se o intervalo [start_time, start_time+duration) esta livre."""
    room_id = request.args.get("room_id")
    date = request.args.get("date")
    start_time = request.args.get("start_time")
    duration_raw = request.args.get("duration_hours", "1")

    if not room_id or not date or not start_time:
        return (
            jsonify(
                {
                    "error": "room_id, date, start_time obrigatorios; duration_hours opcional (1-5)"
                }
            ),
            400,
        )
    try:
        duration_hours = int(duration_raw)
    except ValueError:
        return jsonify({"error": "duration_hours invalido"}), 400
    if duration_hours < 1 or duration_hours > 5:
        return jsonify({"error": "duration_hours deve ser entre 1 e 5"}), 400
    try:
        start_hour = _hour_from_start_time(start_time)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    with _lock:
        ok = _range_available(room_id, date, start_hour, duration_hours)
    return jsonify({"available": ok}), 200


@app.get("/schedule/hourly-matrix")
def hourly_matrix():
    """Uma celula por hora (inicio do expediente ate uma hora antes do fecho) por sala."""
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "date obrigatorio"}), 400

    hours = list(range(OPERATING_START_HOUR, OPERATING_END_HOUR))
    # Mesmo conjunto de salas do room-service.
    room_ids = sorted({"A101", "A102", "B201", "B202"})

    cells: list[dict[str, Any]] = []
    with _lock:
        for room_id in room_ids:
            for h in hours:
                cells.append(
                    {
                        "room_id": room_id,
                        "hour": h,
                        "label": f"{h:02d}:00",
                        "available": _hour_free(room_id, date, h),
                    }
                )

    return (
        jsonify(
            {
                "date": date,
                "hours": hours,
                "room_ids": room_ids,
                "cells": cells,
                "day_start_hour": OPERATING_START_HOUR,
                "day_end_hour": OPERATING_END_HOUR,
            }
        ),
        200,
    )


@app.post("/schedule/bookings")
def create_booking():
    payload = request.get_json(silent=True) or {}
    room_id = payload.get("room_id")
    date = payload.get("date")
    start_time = payload.get("start_time")
    duration_hours = payload.get("duration_hours", 1)
    user_id = payload.get("user_id", "anonymous")
    created_at = payload.get("created_at")

    if not room_id or not date or not start_time:
        return jsonify({"error": "room_id, date, start_time obrigatorios"}), 400
    try:
        duration_hours = int(duration_hours)
    except (TypeError, ValueError):
        return jsonify({"error": "duration_hours invalido"}), 400
    if duration_hours < 1 or duration_hours > 5:
        return jsonify({"error": "duration_hours deve ser entre 1 e 5"}), 400
    try:
        start_hour = _hour_from_start_time(str(start_time))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    with _lock:
        if not _range_available(room_id, date, start_hour, duration_hours):
            return jsonify({"error": "Intervalo indisponivel ou fora do horario"}), 409
        booking = {
            "booking_id": str(uuid.uuid4()),
            "room_id": room_id,
            "date": date,
            "start_hour": start_hour,
            "start_time": f"{start_hour:02d}:00",
            "duration_hours": duration_hours,
            "user_id": user_id,
            "created_at": created_at,
        }
        _bookings.append(booking)
        try:
            _save_disk()
            if _cancel_conflicting_pending(room_id, date, start_hour, duration_hours):
                _save_pending()
            _append_event(
                {
                    "ts": int(time.time()),
                    "type": "booking_created",
                    "booking": booking,
                }
            )
        except OSError as exc:
            _bookings.pop()
            return jsonify({"error": "Falha ao persistir", "details": str(exc)}), 500

    return jsonify({"status": "booked", "booking": booking}), 201


@app.get("/schedule/bookings")
def list_bookings():
    with _lock:
        snapshot = list(_bookings)
    return jsonify({"bookings": snapshot}), 200


@app.post("/schedule/pending")
def create_pending():
    payload = request.get_json(silent=True) or {}
    room_id = payload.get("room_id")
    date = payload.get("date")
    start_time = payload.get("start_time")
    duration_hours = payload.get("duration_hours", 1)
    user_id = payload.get("user_id", "anonymous")
    reason = payload.get("reason", "fallback_room_unavailable")

    if not room_id or not date or not start_time:
        return jsonify({"error": "room_id, date, start_time obrigatorios"}), 400
    try:
        duration_hours = int(duration_hours)
    except (TypeError, ValueError):
        return jsonify({"error": "duration_hours invalido"}), 400
    if duration_hours < 1 or duration_hours > 5:
        return jsonify({"error": "duration_hours deve ser entre 1 e 5"}), 400
    try:
        start_hour = _hour_from_start_time(str(start_time))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    pending = {
        "pending_id": str(uuid.uuid4()),
        "room_id": room_id,
        "date": date,
        "start_hour": start_hour,
        "start_time": f"{start_hour:02d}:00",
        "duration_hours": duration_hours,
        "user_id": user_id,
        "status": "waiting",
        "reason": reason,
        "created_at_ts": int(time.time()),
    }

    with _lock:
        # Evita criar duplicata exata de pendencia ainda esperando.
        duplicate = next(
            (
                p
                for p in _pending
                if p.get("status") == "waiting"
                and p.get("room_id") == room_id
                and p.get("date") == date
                and int(p.get("start_hour", -1)) == start_hour
                and int(p.get("duration_hours", 0)) == duration_hours
                and p.get("user_id") == user_id
            ),
            None,
        )
        if duplicate:
            return jsonify({"status": "already_waiting", "pending": duplicate}), 200

        # Se ja estiver ocupado por reserva existente, a pendencia nasce cancelada por conflito.
        if not _range_available(room_id, date, start_hour, duration_hours):
            pending["status"] = "cancelled_conflict"
            pending["cancel_reason"] = "Conflito: ja existe reserva nesse horario."
            pending["cancelled_at_ts"] = int(time.time())

        _pending.append(pending)
        try:
            _save_pending()
            _append_event(
                {
                    "ts": int(time.time()),
                    "type": "pending_created",
                    "pending": pending,
                }
            )
        except OSError as exc:
            _pending.pop()
            return jsonify({"error": "Falha ao persistir pendencia", "details": str(exc)}), 500

    return jsonify({"status": pending["status"], "pending": pending}), 201


@app.get("/schedule/pending")
def list_pending():
    date_filter = request.args.get("date")
    status_filter = request.args.get("status")
    with _lock:
        snapshot = list(_pending)
    if date_filter:
        snapshot = [p for p in snapshot if p.get("date") == date_filter]
    if status_filter:
        snapshot = [p for p in snapshot if p.get("status") == status_filter]
    return jsonify({"pending": snapshot}), 200


@app.delete("/schedule/bookings/<booking_id>")
def cancel_booking(booking_id: str):
    with _lock:
        idx = next((i for i, b in enumerate(_bookings) if b.get("booking_id") == booking_id), None)
        if idx is None:
            return jsonify({"error": "Reserva nao encontrada"}), 404
        booking = _bookings.pop(idx)
        try:
            _save_disk()
            _append_event(
                {
                    "ts": int(time.time()),
                    "type": "booking_cancelled",
                    "booking": booking,
                }
            )
        except OSError as exc:
            _bookings.insert(idx, booking)
            return jsonify({"error": "Falha ao persistir cancelamento", "details": str(exc)}), 500

    return jsonify({"status": "cancelled", "booking": booking}), 200


@app.get("/schedule/events")
def list_events():
    limit_raw = request.args.get("limit", "100")
    try:
        limit = int(limit_raw)
    except ValueError:
        limit = 100
    limit = max(1, min(limit, 500))

    with _lock:
        snapshot = list(_events)[-limit:]
    return jsonify({"events": snapshot}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=False)
