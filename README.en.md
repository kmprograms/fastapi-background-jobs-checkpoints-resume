# FastAPI Background Jobs — Checkpoints & Resume

**Język / Language:** [Polski](README.md) | **English**

---

A demo of **production patterns for long-running FastAPI background jobs**: `202 Accepted` + status polling, **graceful shutdown** on planned process termination (SIGTERM, deploy), and **SQLite checkpoints** with **auto-resume** after hard kill (`kill -9`, OOM, crash).

---

> 📺 **Prefer watching over reading?** I walk through the entire codebase on YouTube:
> **[Part 1](https://youtu.be/PLACEHOLDER_PART_1)** · **[Part 2](https://youtu.be/PLACEHOLDER_PART_2)**

---

### The problem

Naive `asyncio.create_task()` is fire-and-forget. Task state lives in process RAM — after a restart, deploy, or `kill -9`, the client still sees `running 40%`, but the task is gone or in an inconsistent state.

### Two layers of protection

| Scenario | Cause | Solution |
|----------|-------|----------|
| Planned shutdown | SIGTERM, rolling deploy, Ctrl+C | **Graceful shutdown** — `lifespan`, task registry, wait for in-flight work to finish |
| Immediate termination | `kill -9`, OOM, crash, SIGKILL after grace period | **Checkpoints + resume** — durable state in SQLite outside the process |

### Architecture

```
Client                    FastAPI (uvicorn)                 SQLite
  │                              │                            │
  ├── POST /tasks/video ────────►│ background asyncio task    │
  │◄── 202 + task_id ────────────│                            │
  │                              ├── mark_step() ────────────►│ checkpoint
  ├── GET /tasks/{id} ──────────►│                            │
  │◄── status + progress ────────│                            │
  │                              │                            │
  │         [SIGTERM / deploy]   │ graceful shutdown          │
  │         [kill -9 / crash]    │ process dies               │
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

### Project structure

```
src/disappearing_tasks/
├── api/v1/tasks.py          # REST endpoints
├── services/task_manager.py # BackgroundTaskManager + VideoTaskProcessor
├── infrastructure/sqlite_task_store.py
├── lifespan.py              # startup / shutdown
└── config.py
scripts/
├── demo_client.py           # progress polling
└── demo_hard_kill.py        # kill -9 + resume scenario
```

### Quick start

```bash
# install dependencies
uv sync

# start API server
uv run fastapi-background-jobs-checkpoints-resume
# or: uv run uvicorn disappearing_tasks.main:create_app --factory --reload
```

Default API: `http://127.0.0.1:8000`  
OpenAPI docs: `http://127.0.0.1:8000/docs`

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/v1/health` | Server status, `worker_id`, active task count |
| `POST` | `/api/v1/tasks/video` | Start a task (`202 Accepted`) |
| `GET` | `/api/v1/tasks/{id}` | Task status and progress |
| `GET` | `/api/v1/tasks` | List all tasks |
| `POST` | `/api/v1/tasks/{id}/resume` | Manually resume an interrupted task |

### Demo scenarios

**Graceful shutdown (Ctrl+C):**

```bash
# terminal 1
uv run fastapi-background-jobs-checkpoints-resume

# terminal 2
uv run python scripts/demo_client.py
# while processing: Ctrl+C in terminal 1 — task should finish cleanly
```

**Hard kill + auto-resume:**

```bash
# terminal 1 — server
uv run fastapi-background-jobs-checkpoints-resume

# terminal 2 — script tells you when to kill the process
uv run python scripts/demo_hard_kill.py
# force-kill the process (taskkill /F / kill -9), then start the server again
```

### Configuration

Settings in `config.py` or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `VIDEO_STEPS` | `30` | Number of simulated processing steps |
| `VIDEO_STEP_DELAY_SECONDS` | `2.0` | Delay between steps |
| `SHUTDOWN_TIMEOUT_SECONDS` | `120.0` | Graceful shutdown timeout |
| `DATABASE_PATH` | `data/tasks.db` | Checkpoint database path |
| `AUTO_RESUME_ON_STARTUP` | `true` | Auto-resume orphaned tasks on startup |

### Demo limitations

- No external queue (Redis, RabbitMQ, SQS)
- Single worker — multiple K8s replicas need a distributed lock for resume
- SQLite instead of PostgreSQL/MySQL
- Processing steps assume idempotency

Detailed theory (Polish): [`TEORIA.md`](TEORIA.md)
