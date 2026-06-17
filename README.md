# FastAPI Background Jobs — Checkpoints & Resume

**Język / Language:** **Polski** | [English](README.en.md)

---

Demo produkcyjnego wzorca dla **długich zadań w tle w FastAPI**: kontrakt `202 Accepted` + polling statusu, **graceful shutdown** przy planowanym zatrzymaniu procesu (SIGTERM, deploy) oraz **checkpointy w SQLite** z **auto-resume** po natychmiastowym ubiciu (`kill -9`, OOM, crash).

---

> 📺 **Wolisz obejrzeć niż czytać?** Cały kod omawiam na YouTube:
> **[Część 1](https://youtu.be/PLACEHOLDER_CZESC_1)** · **[Część 2](https://youtu.be/PLACEHOLDER_CZESC_2)**

---

### Problem

Naiwny `asyncio.create_task()` to fire-and-forget. Stan zadania żyje w RAM procesu — po restarcie, deployu lub `kill -9` klient widzi `running 40%`, a zadanie znika albo ma niespójny status.

### Dwie warstwy ochrony

| Scenariusz | Przyczyna | Rozwiązanie |
|------------|-----------|-------------|
| Planowane zatrzymanie | SIGTERM, rolling deploy, Ctrl+C | **Graceful shutdown** — `lifespan`, rejestr tasków, oczekiwanie na dokończenie pracy |
| Natychmiastowe zatrzymanie | `kill -9`, OOM, crash, SIGKILL po grace period | **Checkpointy + resume** — trwały stan w SQLite poza procesem |

### Architektura

```
Klient                    FastAPI (uvicorn)                 SQLite
  │                              │                            │
  ├── POST /tasks/video ────────►│ asyncio task w tle         │
  │◄── 202 + task_id ────────────│                            │
  │                              ├── mark_step() ────────────►│ checkpoint
  ├── GET /tasks/{id} ──────────►│                            │
  │◄── status + postęp ──────────│                            │
  │                              │                            │
  │         [SIGTERM / deploy]   │ graceful shutdown          │
  │         [kill -9 / crash]    │ proces ginie               │
  │                              │                            │
  │         [restart]            │ find_resumable() ◄─────────│
  │                              │ auto-resume ──────────────►│
```

### Tech stack

- Python 3.14+
- FastAPI, Uvicorn
- asyncio
- SQLite (aiosqlite, WAL)
- structlog, pydantic-settings

### Struktura projektu

```
src/disappearing_tasks/
├── api/v1/tasks.py          # endpointy REST
├── services/task_manager.py # BackgroundTaskManager + VideoTaskProcessor
├── infrastructure/sqlite_task_store.py
├── lifespan.py              # startup / shutdown
└── config.py
scripts/
├── demo_client.py           # polling postępu
└── demo_hard_kill.py        # scenariusz kill -9 + resume
```

### Uruchomienie

```bash
# instalacja zależności
uv sync

# serwer API
uv run fastapi-background-jobs-checkpoints-resume
# lub: uv run uvicorn disappearing_tasks.main:create_app --factory --reload
```

API domyślnie: `http://127.0.0.1:8000`  
Dokumentacja OpenAPI: `http://127.0.0.1:8000/docs`

### Endpointy

| Metoda | Ścieżka | Opis |
|--------|---------|------|
| `GET` | `/api/v1/health` | Stan serwera, `worker_id`, liczba aktywnych tasków |
| `POST` | `/api/v1/tasks/video` | Uruchom zadanie (`202 Accepted`) |
| `GET` | `/api/v1/tasks/{id}` | Status i postęp zadania |
| `GET` | `/api/v1/tasks` | Lista wszystkich zadań |
| `POST` | `/api/v1/tasks/{id}/resume` | Ręczne wznowienie przerwanego zadania |

### Scenariusze demo

**Graceful shutdown (Ctrl+C):**

```bash
# terminal 1
uv run fastapi-background-jobs-checkpoints-resume

# terminal 2
uv run python scripts/demo_client.py
# w trakcie przetwarzania: Ctrl+C w terminalu 1 — task powinien się dokończyć
```

**Hard kill + auto-resume:**

```bash
# terminal 1 — serwer
uv run fastapi-background-jobs-checkpoints-resume

# terminal 2 — skrypt podpowie kiedy zabić proces
uv run python scripts/demo_hard_kill.py
# zabij proces na siłę (taskkill /F / kill -9), potem uruchom serwer ponownie
```

### Konfiguracja

Ustawienia w `config.py` lub pliku `.env`:

| Zmienna | Domyślnie | Opis |
|---------|-----------|------|
| `VIDEO_STEPS` | `30` | Liczba etapów symulowanego przetwarzania |
| `VIDEO_STEP_DELAY_SECONDS` | `2.0` | Opóźnienie między etapami |
| `SHUTDOWN_TIMEOUT_SECONDS` | `120.0` | Timeout graceful shutdown |
| `DATABASE_PATH` | `data/tasks.db` | Ścieżka do bazy checkpointów |
| `AUTO_RESUME_ON_STARTUP` | `true` | Auto-wznowienie osieroconych zadań |

### Ograniczenia demo

- Brak kolejki zewnętrznej (Redis, RabbitMQ, SQS)
- Jeden worker — przy wielu replikach K8s potrzebny distributed lock na resume
- SQLite zamiast PostgreSQL/MySQL
- Etapy przetwarzania zakładają idempotentność

Szczegółowa teoria: [`TEORIA.md`](TEORIA.md)
