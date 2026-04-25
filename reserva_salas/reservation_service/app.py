import os
import time
import uuid
from datetime import date as date_only
from typing import Any, Optional

import requests
from flask import Flask, jsonify, request

app = Flask(__name__)

ROOM_SERVICE_URL = os.getenv("ROOM_SERVICE_URL", "http://localhost:5001")
SCHEDULE_SERVICE_URL = os.getenv("SCHEDULE_SERVICE_URL", "http://schedule-service:5002")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "2"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "3"))
RETRY_DELAY_SECONDS = float(os.getenv("RETRY_DELAY_SECONDS", "0.5"))

# Idempotency store em memoria.
# Em producao, use Redis ou banco compartilhado.
idempotency_store: dict[str, dict[str, Any]] = {}


def _parse_iso_date(value: str) -> Optional[date_only]:
    try:
        parts = value.strip().split("-")
        if len(parts) != 3:
            return None
        y, m, d = int(parts[0]), int(parts[1]), int(parts[2])
        return date_only(y, m, d)
    except ValueError:
        return None


INDEX_HTML = """
<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Reserva de Salas</title>
  <style>
    :root {
      --card: #1a2332;
      --border: #2d3a4d;
      --text: #e8edf4;
      --muted: #8b9cb3;
      --free: #16a34a;
      --free-hover: #22c55e;
      --taken: #dc2626;
      --taken-border: #991b1b;
      --sel: #3b82f6;
    }
    * { box-sizing: border-box; }
    body {
      font-family: "Segoe UI", system-ui, sans-serif;
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(160deg, #0f1419 0%, #1a1f2e 50%, #121826 100%);
      color: var(--text);
    }
    .wrap { max-width: 1200px; margin: 0 auto; padding: 28px 20px 48px; }
    h1 { font-size: 1.75rem; font-weight: 700; margin: 0 0 4px; letter-spacing: -0.02em; }
    .subtitle { color: var(--muted); font-size: 0.95rem; margin: 0 0 24px; }
    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 20px 22px;
      margin-top: 16px;
      box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    }
    .card h3 { margin: 0 0 14px; font-size: 1.1rem; }
    .row { display: flex; gap: 12px; flex-wrap: wrap; align-items: flex-end; }
    .field { display: flex; flex-direction: column; gap: 6px; min-width: 140px; }
    label { font-size: 0.8rem; color: var(--muted); text-transform: uppercase; letter-spacing: 0.04em; }
    input[type="date"], input[type="text"], select {
      padding: 10px 12px; font-size: 14px; border-radius: 8px; border: 1px solid var(--border);
      background: #0d1218; color: var(--text);
    }
    .btn {
      padding: 10px 20px; font-size: 14px; font-weight: 600; border: none; border-radius: 8px;
      cursor: pointer; background: var(--sel); color: #fff;
    }
    .btn:hover { filter: brightness(1.1); }
    .btn:disabled { opacity: 0.5; cursor: not-allowed; }
    .legend { display: flex; gap: 20px; flex-wrap: wrap; margin-top: 14px; font-size: 0.85rem; }
    .legend span { display: flex; align-items: center; gap: 8px; }
    .leg-box { width: 20px; height: 20px; border-radius: 4px; border: 2px solid rgba(255,255,255,0.15); }
    .leg-free { background: var(--free); }
    .leg-taken { background: var(--taken); border-color: var(--taken-border); }
    .map-wrap { overflow-x: auto; margin-top: 12px; -webkit-overflow-scrolling: touch; }
    .seat-map { border-collapse: separate; border-spacing: 4px; min-width: 100%; }
    .seat-map th.corner { width: 88px; }
    .seat-map th { font-size: 0.65rem; font-weight: 600; color: var(--muted); padding: 2px; text-align: center; max-width: 56px; }
    .seat-map td.row-label {
      font-weight: 600; font-size: 0.85rem; text-align: right; padding-right: 6px; white-space: nowrap;
    }
    .cell {
      width: 52px; min-width: 48px; height: 40px; border-radius: 6px; text-align: center; vertical-align: middle;
      font-size: 0.6rem; font-weight: 600; cursor: default; user-select: none;
      border: 2px solid rgba(0,0,0,0.2); transition: transform 0.12s, box-shadow 0.12s;
    }
    .cell-free { background: var(--free); color: #fff; cursor: pointer; }
    .cell-free:hover { background: var(--free-hover); transform: scale(1.05); box-shadow: 0 4px 12px rgba(34,197,94,0.35); }
    .cell-taken { background: var(--taken); color: #fecaca; border-color: var(--taken-border); }
    .cell-sel { outline: 3px solid var(--sel); outline-offset: 1px; }
    .hint { color: var(--muted); font-size: 0.86rem; margin: 0 0 10px; }
    .date-br-display { margin: 6px 0 0; font-size: 1.05rem; font-weight: 600; color: var(--text); letter-spacing: 0.02em; }
    .date-br-note { margin: 4px 0 0; font-size: 0.78rem; color: var(--muted); }
    pre { background: #0d1218; border: 1px solid var(--border); border-radius: 8px; padding: 14px; overflow: auto; font-size: 0.8rem; }
    .grid-2 { display: grid; grid-template-columns: 1fr; gap: 16px; }
    @media (min-width: 980px) { .grid-2 { grid-template-columns: 1fr 1fr; } }
    .list { margin-top: 10px; display: grid; gap: 10px; }
    .item { background: #0d1218; border: 1px solid var(--border); border-radius: 10px; padding: 12px; }
    .item-top { display: flex; justify-content: space-between; gap: 10px; align-items: center; flex-wrap: wrap; }
    .pill { font-size: 0.72rem; color: var(--muted); border: 1px solid var(--border); padding: 4px 8px; border-radius: 999px; }
    .btn-danger { background: #ef4444; }
    .btn-danger:hover { filter: brightness(1.05); }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Mapa de reservas</h1>
    <p class="subtitle">Cada coluna é <strong>1 hora</strong>. Escolha quantas horas seguidas quer (1 a 5), clique na <strong>primeira hora</strong> da sala e confirme. Só é permitido entre <strong>9h e 23h</strong> (nada antes das 9h nem depois das 23h). Reservas ficam salvas em disco no servidor.</p>

    <div class="card">
      <h3>1. Data e duração</h3>
      <div class="row">
        <div class="field" style="min-width: 220px;">
          <label for="date">Data (dia / mês / ano)</label>
          <input id="date" type="date" required />
          <div class="date-br-display" id="dateBrDisplay" aria-live="polite"></div>
          <p class="date-br-note">Não é possível escolher ontem nem datas anteriores — só a partir de hoje.</p>
        </div>
        <div class="field">
          <label for="durationHours">Duração da reserva</label>
          <select id="durationHours">
            <option value="1">1 hora</option>
            <option value="2">2 horas</option>
            <option value="3">3 horas</option>
            <option value="4">4 horas</option>
            <option value="5">5 horas</option>
          </select>
        </div>
      </div>
      <p class="legend">
        <span><span class="leg-box leg-free"></span> Hora livre</span>
        <span><span class="leg-box leg-taken"></span> Hora ocupada</span>
      </p>
      <p class="hint">Reservas só entre <strong>09:00</strong> e <strong>23:00</strong> (o fim do bloco não pode passar das 23h; a última hora de início depende da duração).</p>
      <div id="mapContainer" class="map-wrap">Escolha a data e clique em <strong>Atualizar mapa</strong>.</div>
    </div>

    <div class="grid-2">
      <div class="card">
        <h3>2. Confirmar reserva</h3>
        <p class="hint" id="selectionHint">Defina a duração, depois clique na <strong>primeira hora livre</strong> da sala desejada.</p>
        <div class="row">
          <div class="field" style="min-width: 220px;">
            <label for="userId">Seu nome ou ID</label>
            <input id="userId" type="text" value="" placeholder="Ex.: João" />
          </div>
          <div class="field" style="justify-content: flex-end;">
            <label>&nbsp;</label>
            <button type="button" class="btn" id="btnRes" onclick="reserve()" disabled>Reservar seleção</button>
          </div>
        </div>
        <h4 style="margin: 20px 0 8px; font-size: 0.9rem; color: var(--muted);">Resposta do servidor</h4>
        <pre id="output">Nenhum pedido ainda.</pre>
      </div>

      <div class="card">
        <h3>Reservas do dia</h3>
        <p class="hint">Lista de reservas para a data selecionada. Você pode <strong>desmarcar</strong> uma reserva aqui.</p>
        <div id="bookingsList" class="list">Carregando...</div>
      </div>
    </div>

    <div class="card">
      <h3>Lista de espera (pendências)</h3>
      <p class="hint">Pedidos que entraram em fallback ficam aqui aguardando. Se o mesmo horário for reservado, a pendência vira <strong>cancelled_conflict</strong>.</p>
      <div id="pendingList" class="list">Carregando...</div>
    </div>

    <div class="card">
      <h3>Log de eventos</h3>
      <p class="hint">Últimas ações registradas (criação/cancelamento).</p>
      <pre id="eventsLog">Carregando...</pre>
    </div>
  </div>

  <script>
    let matrixData = null;
    let freeMap = {};
    let selected = null;

    function newIdemKey() {
      if (window.crypto && typeof crypto.randomUUID === "function") {
        return crypto.randomUUID();
      }
      return "req-" + Date.now() + "-" + Math.random().toString(16).slice(2, 10);
    }

    function getDuration() {
      return parseInt(document.getElementById("durationHours").value, 10) || 1;
    }

    function getDayEnd() {
      return matrixData && matrixData.day_end_hour ? matrixData.day_end_hour : 23;
    }

    function todayISO() {
      var d = new Date();
      return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
    }

    function updateDateBrDisplay() {
      var el = document.getElementById("date");
      var out = document.getElementById("dateBrDisplay");
      if (!el.value) {
        out.textContent = "";
        return;
      }
      var p = el.value.split("-");
      if (p.length !== 3) {
        out.textContent = "";
        return;
      }
      var dt = new Date(parseInt(p[0], 10), parseInt(p[1], 10) - 1, parseInt(p[2], 10));
      out.textContent = dt.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit", year: "numeric" });
    }

    function refreshDateMin() {
      var el = document.getElementById("date");
      var t = todayISO();
      el.min = t;
      if (!el.value || el.value < t) {
        el.value = t;
      }
      updateDateBrDisplay();
    }

    var dateInput = document.getElementById("date");
    refreshDateMin();
    dateInput.addEventListener("change", refreshDateMin);
    dateInput.addEventListener("input", updateDateBrDisplay);

    function cellId(room, hour) {
      return room + "||" + hour;
    }

    function isRangeFree(room, startH, dur) {
      const end = getDayEnd();
      if (startH + dur > end) return false;
      for (let h = startH; h < startH + dur; h++) {
        if (!freeMap[room] || freeMap[room][h] !== true) return false;
      }
      return true;
    }

    function applySelection() {
      document.querySelectorAll(".cell-sel").forEach((el) => el.classList.remove("cell-sel"));
      const btn = document.getElementById("btnRes");
      const hint = document.getElementById("selectionHint");
      if (selected) {
        const dur = selected.duration;
        for (let h = selected.startHour; h < selected.startHour + dur; h++) {
          const el = document.querySelector('[data-cid="' + cellId(selected.room, h) + '"]');
          if (el) el.classList.add("cell-sel");
        }
        btn.disabled = false;
        const st = String(selected.startHour).padStart(2, "0") + ":00";
        hint.textContent = "Sala " + selected.room + " — de " + st + " por " + dur + " h. Ajuste o nome e clique em Reservar.";
      } else {
        btn.disabled = true;
        hint.textContent = "Defina a duração, depois clique na primeira hora livre da sala.";
      }
    }

    document.getElementById("durationHours").addEventListener("change", function () {
      selected = null;
      applySelection();
      scheduleAutoRefresh();
    });

    let _refreshTimer = null;
    function scheduleAutoRefresh() {
      if (_refreshTimer) clearTimeout(_refreshTimer);
      _refreshTimer = setTimeout(function () {
        loadMatrix();
      }, 250);
    }

    async function loadMatrix() {
      refreshDateMin();
      const date = document.getElementById("date").value;
      const cont = document.getElementById("mapContainer");
      cont.innerHTML = "Carregando mapa...";
      selected = null;
      applySelection();

      const resp = await fetch("/rooms/availability-matrix?date=" + encodeURIComponent(date));
      const data = await resp.json();
      if (!resp.ok) {
        cont.innerHTML = "<p style=\\"color:#f87171\\">Erro: " + (data.error || "falha") + "</p>";
        matrixData = null;
        freeMap = {};
        await loadBookings();
        await loadPending();
        await loadEvents();
        return;
      }
      matrixData = data;
      freeMap = {};
      (data.cells || []).forEach(function (c) {
        if (!freeMap[c.room_id]) freeMap[c.room_id] = {};
        freeMap[c.room_id][c.hour] = !!c.available;
      });

      const rooms = data.room_ids || [];
      const hours = data.hours || [];

      let html = "<table class=\\"seat-map\\" role=\\"grid\\" aria-label=\\"Mapa hora a hora\\">";
      html += "<thead><tr><th class=\\"corner\\"></th>";
      hours.forEach(function (h) {
        html += "<th>" + String(h).padStart(2, "0") + "h</th>";
      });
      html += "</tr></thead><tbody>";

      rooms.forEach(function (room) {
        html += "<tr><td class=\\"row-label\\">Sala " + room + "</td>";
        hours.forEach(function (h) {
          const av = freeMap[room] && freeMap[room][h];
          const cid = cellId(room, h);
          const cls = av ? "cell cell-free" : "cell cell-taken";
          const label = av ? "Livre" : "Ocup.";
          if (av) {
            html += "<td class=\\"" + cls + "\\" data-cid=\\"" + cid + "\\" data-room=\\"" + room + "\\" data-hour=\\"" + h + "\\" title=\\"Inicio da reserva\\">" + label + "</td>";
          } else {
            html += "<td class=\\"" + cls + "\\" data-cid=\\"" + cid + "\\" title=\\"Ocupado\\">" + label + "</td>";
          }
        });
        html += "</tr>";
      });
      html += "</tbody></table>";
      cont.innerHTML = html;

      cont.querySelectorAll(".cell-free").forEach(function (td) {
        td.addEventListener("click", function () {
          const room = this.getAttribute("data-room");
          const startH = parseInt(this.getAttribute("data-hour"), 10);
          const dur = getDuration();
          if (!isRangeFree(room, startH, dur)) {
            document.getElementById("selectionHint").textContent =
              "Esse inicio nao cabe: alguma hora do intervalo esta ocupada ou passa do horario (ate " + getDayEnd() + "h).";
            selected = null;
            applySelection();
            return;
          }
          selected = { room: room, startHour: startH, duration: dur };
          applySelection();
        });
      });

      await loadBookings();
      await loadPending();
      await loadEvents();
    }

    async function loadBookings() {
      const date = document.getElementById("date").value;
      const el = document.getElementById("bookingsList");
      el.textContent = "Carregando...";
      const resp = await fetch("/bookings?date=" + encodeURIComponent(date));
      const data = await resp.json();
      if (!resp.ok) {
        el.innerHTML = "<div class=\\"item\\">Erro ao carregar reservas.</div>";
        return;
      }
      const bookings = data.bookings || [];
      if (!bookings.length) {
        el.innerHTML = "<div class=\\"item\\">Nenhuma reserva para este dia.</div>";
        return;
      }
      el.innerHTML = bookings.map(function (b) {
        const endH = (b.start_hour + b.duration_hours);
        const range = String(b.start_hour).padStart(2, "0") + ":00–" + String(endH).padStart(2, "0") + ":00";
        return (
          "<div class=\\"item\\">" +
            "<div class=\\"item-top\\">" +
              "<div><strong>Sala " + b.room_id + "</strong> <span class=\\"pill\\">" + range + "</span></div>" +
              "<button class=\\"btn btn-danger\\" onclick=\\"cancelBooking('" + b.booking_id + "')\\">Desmarcar</button>" +
            "</div>" +
            "<div style=\\"margin-top:6px;color:var(--muted);font-size:0.85rem\\">" +
              "Reserva: " + b.booking_id + " • Usuário: " + (b.user_id || "anonymous") +
            "</div>" +
          "</div>"
        );
      }).join("");
    }

    async function cancelBooking(id) {
      if (!id) return;
      const output = document.getElementById("output");
      output.textContent = "Cancelando reserva...";
      const resp = await fetch("/bookings/" + encodeURIComponent(id), { method: "DELETE" });
      const data = await resp.json();
      output.textContent = JSON.stringify({ status: resp.status, ...data }, null, 2);
      await loadMatrix();
    }

    async function loadEvents() {
      const el = document.getElementById("eventsLog");
      const resp = await fetch("/events?limit=60");
      const data = await resp.json();
      if (!resp.ok) {
        el.textContent = "Erro ao carregar eventos.";
        return;
      }
      const events = data.events || [];
      if (!events.length) {
        el.textContent = "Sem eventos ainda.";
        return;
      }
      el.textContent = events.map(function (e) {
        const dt = new Date((e.ts || 0) * 1000);
        const when = dt.toLocaleString("pt-BR");
        const b = e.booking || {};
        const info = b.booking_id ? (" booking_id=" + b.booking_id + " sala=" + b.room_id + " " + b.start_time + " dur=" + b.duration_hours + "h") : "";
        return "[" + when + "] " + e.type + info;
      }).join("\\n");
    }

    async function loadPending() {
      const date = document.getElementById("date").value;
      const el = document.getElementById("pendingList");
      const resp = await fetch("/pending?date=" + encodeURIComponent(date));
      const data = await resp.json();
      if (!resp.ok) {
        el.innerHTML = "<div class=\\"item\\">Erro ao carregar pendências.</div>";
        return;
      }
      const rows = data.pending || [];
      if (!rows.length) {
        el.innerHTML = "<div class=\\"item\\">Sem pendências para este dia.</div>";
        return;
      }
      el.innerHTML = rows.map(function (p) {
        const endH = (p.start_hour + p.duration_hours);
        const range = String(p.start_hour).padStart(2, "0") + ":00–" + String(endH).padStart(2, "0") + ":00";
        return (
          "<div class=\\"item\\">" +
            "<div class=\\"item-top\\">" +
              "<div><strong>Sala " + p.room_id + "</strong> <span class=\\"pill\\">" + range + "</span></div>" +
              "<span class=\\"pill\\">status: " + p.status + "</span>" +
            "</div>" +
            "<div style=\\"margin-top:6px;color:var(--muted);font-size:0.85rem\\">" +
              "Pendência: " + p.pending_id + " • Usuário: " + (p.user_id || "anonymous") +
            "</div>" +
          "</div>"
        );
      }).join("");
    }

    async function reserve() {
      if (!selected) return;
      refreshDateMin();
      const date = document.getElementById("date").value;
      const user = document.getElementById("userId").value.trim() || "anonymous";
      const output = document.getElementById("output");
      output.textContent = "Enviando...";

      const idem = newIdemKey();
      const startTime = String(selected.startHour).padStart(2, "0") + ":00";
      const body = {
        room_id: selected.room,
        date: date,
        start_time: startTime,
        duration_hours: selected.duration,
        user_id: user
      };
      const resp = await fetch("/reservations", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": idem
        },
        body: JSON.stringify(body)
      });
      const data = await resp.json();
      output.textContent = JSON.stringify({ status: resp.status, ...data }, null, 2);
      if (resp.ok && (resp.status === 201 || data.status === "confirmed" || (data.message && !data.fallback_triggered))) {
        await loadMatrix();
        selected = null;
        applySelection();
      }
    }

    // Auto atualizar quando mudar a data (sem botao).
    dateInput.addEventListener("change", function () {
      refreshDateMin();
      scheduleAutoRefresh();
    });

    // Primeira carga automatica.
    scheduleAutoRefresh();
  </script>
</body>
</html>
"""


@app.get("/health")
def health() -> tuple[dict[str, str], int]:
    return {"status": "ok", "service": "reservation-service"}, 200


@app.get("/")
def index():
    return INDEX_HTML, 200, {"Content-Type": "text/html; charset=utf-8"}


@app.get("/rooms/availability")
def list_rooms_availability():
    date = request.args.get("date")
    start_time = request.args.get("start_time")
    duration_hours = request.args.get("duration_hours", "1")

    if not date or not start_time:
        return jsonify({"error": "Campos obrigatorios: date, start_time"}), 400

    try:
        response = requests.get(
            f"{ROOM_SERVICE_URL}/rooms/availability",
            params={
                "date": date,
                "start_time": start_time,
                "duration_hours": duration_hours,
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        return jsonify(response.json()), 200
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar Room Service", "details": str(exc)}), 503


@app.get("/rooms/availability-matrix")
def list_rooms_availability_matrix():
    date = request.args.get("date")
    if not date:
        return jsonify({"error": "Campos obrigatorios: date"}), 400

    try:
        response = requests.get(
            f"{ROOM_SERVICE_URL}/rooms/availability-matrix",
            params={"date": date},
            timeout=30.0,
        )
        response.raise_for_status()
        return jsonify(response.json()), 200
    except requests.RequestException as exc:
        return (
            jsonify(
                {
                    "error": "Falha ao consultar Room Service (matriz)",
                    "details": str(exc),
                }
            ),
            503,
        )


@app.get("/bookings")
def list_bookings_proxy():
    date = request.args.get("date")
    try:
        resp = requests.get(f"{ROOM_SERVICE_URL}/rooms/bookings", timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar reservas", "details": str(exc)}), 503

    bookings = data.get("bookings", [])
    if date:
        bookings = [b for b in bookings if b.get("date") == date]
    return jsonify({"bookings": bookings}), 200


@app.delete("/bookings/<booking_id>")
def cancel_booking_proxy(booking_id: str):
    try:
        resp = requests.delete(f"{ROOM_SERVICE_URL}/rooms/bookings/{booking_id}", timeout=10)
        return jsonify(resp.json()), resp.status_code
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao cancelar reserva", "details": str(exc)}), 503


@app.get("/events")
def list_events_proxy():
    limit = request.args.get("limit", "100")
    try:
        resp = requests.get(f"{ROOM_SERVICE_URL}/rooms/events", params={"limit": limit}, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json()), 200
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar eventos", "details": str(exc)}), 503


@app.get("/pending")
def list_pending_proxy():
    date = request.args.get("date")
    status = request.args.get("status")
    params: dict[str, str] = {}
    if date:
        params["date"] = date
    if status:
        params["status"] = status
    try:
        resp = requests.get(f"{SCHEDULE_SERVICE_URL}/schedule/pending", params=params, timeout=10)
        resp.raise_for_status()
        return jsonify(resp.json()), 200
    except requests.RequestException as exc:
        return jsonify({"error": "Falha ao consultar pendencias", "details": str(exc)}), 503


@app.post("/reservations")
def create_reservation():
    payload = request.get_json(silent=True) or {}
    room_id = payload.get("room_id")
    date_str = payload.get("date")
    start_time = payload.get("start_time")
    duration_hours = payload.get("duration_hours", 1)
    user_id = payload.get("user_id", "anonymous")

    if not room_id or not date_str or not start_time:
        return (
            jsonify(
                {"error": "Campos obrigatorios: room_id, date, start_time, duration_hours (1-5)"}
            ),
            400,
        )

    booking_date = _parse_iso_date(str(date_str))
    if booking_date is None:
        return jsonify({"error": "Data invalida (use AAAA-MM-DD)"}), 400
    if booking_date < date_only.today():
        return jsonify({"error": "Nao e permitido reservar datas passadas (nem ontem)"}), 400
    try:
        duration_hours = int(duration_hours)
    except (TypeError, ValueError):
        return jsonify({"error": "duration_hours invalido"}), 400
    if duration_hours < 1 or duration_hours > 5:
        return jsonify({"error": "duration_hours deve ser entre 1 e 5"}), 400

    idem_key = request.headers.get("Idempotency-Key")
    if not idem_key:
        return jsonify({"error": "Header obrigatorio: Idempotency-Key"}), 400

    if idem_key in idempotency_store:
        saved_response = idempotency_store[idem_key]
        return jsonify(saved_response), 200

    body = {
        "room_id": room_id,
        "date": date_str,
        "start_time": start_time,
        "duration_hours": duration_hours,
        "user_id": user_id,
    }
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(
                f"{ROOM_SERVICE_URL}/rooms/check-and-book",
                json=body,
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            room_result = response.json()

            reservation = {
                "reservation_id": str(uuid.uuid4()),
                "room_id": room_id,
                "date": date_str,
                "start_time": start_time,
                "duration_hours": duration_hours,
                "user_id": user_id,
                "status": room_result.get("status", "confirmed"),
                "message": room_result.get("message", "Reserva criada com sucesso"),
                "attempts_used": attempt,
            }

            idempotency_store[idem_key] = reservation
            return jsonify(reservation), 201

        except requests.Timeout:
            last_error = "timeout"
        except requests.RequestException as exc:
            last_error = str(exc)

        if attempt < MAX_RETRIES:
            time.sleep(RETRY_DELAY_SECONDS)

    fallback_response = {
        "reservation_id": None,
        "status": "pending_manual_review",
        "message": (
            "Servico de sala indisponivel. Pedido registrado para tratamento posterior."
        ),
        "fallback_triggered": True,
        "last_error": last_error,
    }

    # Persistir pedido na lista de espera, para sobreviver reinicio do servico.
    try:
        pending_resp = requests.post(
            f"{SCHEDULE_SERVICE_URL}/schedule/pending",
            json={
                "room_id": room_id,
                "date": date_str,
                "start_time": start_time,
                "duration_hours": duration_hours,
                "user_id": user_id,
                "reason": "fallback_room_unavailable",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        if pending_resp.ok:
            pending_payload = pending_resp.json()
            fallback_response["pending_registered"] = True
            fallback_response["pending"] = pending_payload.get("pending")
        else:
            fallback_response["pending_registered"] = False
    except requests.RequestException:
        fallback_response["pending_registered"] = False

    idempotency_store[idem_key] = fallback_response
    return jsonify(fallback_response), 202


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
