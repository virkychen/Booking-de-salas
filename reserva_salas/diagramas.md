# Modelagem do sistema distribuido - Reserva de Salas

## 1) Componentes

```mermaid
flowchart LR
    U[Usuario] --> RS[Reservation Service]
    RS --> RMS[Room Service]
    RMS --> SS[Schedule Service]
```

## 2) Fluxo real de reserva

```mermaid
sequenceDiagram
    participant U as Usuario
    participant RS as Reservation Service
    participant RMS as Room Service
    participant SS as Schedule Service

    U->>RS: 1. Solicita reserva (room_id, data, horario)
    RS->>RMS: 2. Verifica disponibilidade (com timeout/retry)
    RMS->>SS: 3. Consulta disponibilidade
    SS-->>RMS: Livre/Ocupada
    alt Sala livre
        RMS->>SS: 4. Registra reserva
        SS-->>RMS: Reserva confirmada
        RMS-->>RS: Confirmada
        RS-->>U: Reserva criada
    else Sala ocupada
        RMS-->>RS: Indisponivel
        RS-->>U: Falha de reserva
    end
```

## 3) Resiliencia e seguranca (consistencia operacional)

- Retry: `Reservation Service` tenta novamente ao chamar `Room Service`.
- Timeout: `Reservation Service` limita tempo de espera da resposta.
- Fallback: se `Room Service` falhar apos retries, pedido entra em `pending_manual_review`.
- Idempotencia: `Reservation Service` exige `Idempotency-Key` para evitar reservas duplicadas em reenvios.
