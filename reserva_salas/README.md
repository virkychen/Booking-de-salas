# Sistema de Reserva de Salas (Distribuido)

Projeto em microsservicos para reserva de salas de reuniao, implementando os requisitos:

- Retry (Reservation -> Room)
- Timeout (Reservation aguardando Room)
- Fallback (Reservation quando Room indisponivel)
- Idempotencia (na criacao da reserva)

## Arquitetura

- `reservation_service` (porta 5000): orquestra reserva e aplica resiliencia.
- `room_service` (porta 5001): valida sala e coordena com agenda.
- `schedule_service` (porta 5002): gerencia disponibilidade e registro de reservas.

O sistema e distribuido em servicos separados, cada um em seu container Docker, evitando arquitetura monolitica.

## Persistencia das reservas e pendencias

As reservas ficam em arquivo JSON dentro do volume Docker `schedule_data` (caminho no container: `/data/bookings.json`). As pendencias de fallback ficam em `/data/pending.json` e o log em `/data/events.jsonl`.

Se voce parar os containers e subir de novo, **dados continuam** (o volume nao e apagado com `docker compose down`; use `docker compose down -v` se quiser limpar).

## Como executar

Pre-requisito: Docker Desktop com `docker compose`.

```bash
docker compose up --build
```

## Interface web (mapa de reservas)

Abra [http://localhost:5000](http://localhost:5000) no navegador. O mapa mostra **uma coluna por hora** (09h a 22h), salas nas linhas: verde = hora livre, vermelho = hora ocupada. Escolha **1 a 5 horas** de duracao, clique na **primeira hora** do bloco desejado e confirme. A chave de idempotencia continua sendo gerada no navegador (nao aparece na tela).

Janela permitida: **09:00 ate 23:00** (nao reserva antes das 9h; o fim da reserva nao pode ultrapassar as 23h). Ajuste via `OPERATING_START_HOUR` e `OPERATING_END_HOUR` no servico de agenda.

## Testes do fluxo

### 1) Reserva com sucesso

```bash
curl -X POST http://localhost:5000/reservations ^
  -H "Content-Type: application/json" ^
  -H "Idempotency-Key: req-123" ^
  -d "{\"room_id\":\"A101\",\"date\":\"2026-04-25\",\"start_time\":\"10:00\",\"duration_hours\":2,\"user_id\":\"u1\"}"
```

### 2) Idempotencia (mesmo Idempotency-Key)

Repita a mesma chamada acima com `Idempotency-Key: req-123`.
Resultado esperado: nao cria nova reserva, retorna o mesmo payload salvo.

### 3) Conflito de horario

Tente reservar novamente a mesma sala/data/horario com outro `Idempotency-Key`.
Resultado esperado: conflito (indisponivel).

### 4) Fallback (simulando falha do Room Service)

Pare o container `room-service` e faca nova solicitacao:

```bash
docker stop room-service
```

Depois chame `POST /reservations` com novo `Idempotency-Key`.
Resultado esperado: resposta `202` com status `pending_manual_review`, `fallback_triggered: true` e pendencia registrada na lista de espera.

## Endpoints principais

- Reservation Service
  - `GET /` (interface web)
  - `GET /health`
  - `GET /pending?date=YYYY-MM-DD`
  - `GET /rooms/availability` (parametros: `date`, `start_time`, `duration_hours` opcional 1-5)
  - `GET /rooms/availability-matrix?date=YYYY-MM-DD` (mapa hora a hora)
  - `POST /reservations` (JSON: `room_id`, `date`, `start_time`, `duration_hours`, `user_id`)
- Room Service
  - `GET /health`
  - `GET /rooms/availability`
  - `GET /rooms/availability-matrix?date=YYYY-MM-DD`
  - `POST /rooms/check-and-book`
- Schedule Service
  - `GET /health`
  - `GET /schedule/availability` (`room_id`, `date`, `start_time`, `duration_hours`)
  - `GET /schedule/hourly-matrix?date=YYYY-MM-DD`
  - `POST /schedule/pending`
  - `GET /schedule/pending`
  - `POST /schedule/bookings`
  - `GET /schedule/bookings`

## Modelagem

Consulte `diagramas.md` para os diagramas de componentes e sequencia.
