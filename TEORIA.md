## Wzorzec API dla długotrwałej pracy

API, które uruchamia **długi job w tle**, nie może blokować klienta na czas trwania całego pipeline'u. Request HTTP ma limit czasu. Load balancer kończy połączenie. Klient nie będzie czekał minutę na odpowiedź.

Standardowy wzorzec produkcyjny wygląda tak:

1. Klient wysyła `POST /tasks/video` z parametrami pliku.
2. Serwer odpowiada natychmiast kodem `202 Accepted` i zwraca `task_id`.
3. Przetwarzanie uruchamia się w tle jako osobna korutyna `asyncio`.
4. Klient odpytuje `GET /tasks/{id}` i odczytuje postęp: status, procent ukończenia, błąd.

Ten sam kontrakt HTTP stosują wewnętrzne systemy raportowe, API jobów w chmurze i usługi z długim czasem odpowiedzi. Klient dostaje identyfikator zadania i polling statusu zamiast synchronicznego oczekiwania.

W produkcji pojawia się inne pytanie: co dzieje się z zadaniem i danymi klienta, gdy proces aplikacji zostaje zatrzymany w trakcie przetwarzania.

Zatrzymanie procesu w produkcji to standardowa operacja, nie wyjątek. Rolling deploy w Kubernetes, scale-down autoscalera, restart po zmianie konfiguracji, OOM kill — to scenariusze, które występują regularnie.

---

## Dwa scenariusze zatrzymania procesu

Proces aplikacji może zostać zatrzymany na dwa sposoby. Każdy wymaga innej warstwy ochrony danych.

### Scenariusz A — planowane zatrzymanie (SIGTERM)

Orchestrator — Kubernetes, Docker, systemd — wysyła procesowi sygnał `SIGTERM`. Proces dostaje określony czas na zakończenie pracy przed wymuszonym ubiciem.

Typowe przyczyny:
- rolling deploy nowej wersji aplikacji
- scale-down liczby replik
- restart po aktualizacji konfiguracji
- lokalne `Ctrl+C` w terminalu (SIGINT, obsługiwany przez uvicorn podobnie do graceful shutdown)

Sekwencja zdarzeń:

1. System wysyła `SIGTERM` do procesu uvicorn.
2. Uvicorn przestaje przyjmować nowe połączenia HTTP.
3. FastAPI wchodzi w fazę shutdown context managera `lifespan` — wykonuje kod po instrukcji `yield`.
4. Aplikacja decyduje: czeka na zakończenie tasków w tle, zamyka połączenia do bazy, flushuje bufory.
5. Jeśli zakończenie mieści się w czasie — proces wychodzi z kodem 0.
6. Jeśli nie — po upływie `terminationGracePeriodSeconds` w Kubernetes następuje `SIGKILL`.

Na ten scenariusz odpowiada **graceful shutdown**: kontrolowane oczekiwanie na dokończenie pracy w toku przed zamknięciem procesu.

### Scenariusz B — natychmiastowe zatrzymanie (SIGKILL, OOM, crash)

Proces nie dostaje czasu na wykonanie kodu shutdown. Nie uruchamia się blok po `yield` w `lifespan`. Nie wykonuje się `task_manager.shutdown()`. Event loop przestaje istnieć w tej samej chwili.

Typowe przyczyny:
- `kill -9`
- OOM killer kernela
- segfault, panic, nieobsłużony crash runtime
- `SIGKILL` po przekroczeniu grace period w Kubernetes

Na ten scenariusz graceful shutdown nie ma zastosowania. Jedyna ochrona to **trwały stan zadania poza procesem** — zapis w bazie danych, kolejce lub na dysku.

Sam `asyncio.create_task()` bez dodatkowej architektury nie chroni przed żadnym z tych scenariuszy. Sam graceful shutdown chroni przed planowanym deployem, ale nie przed `kill -9`. W systemach produkcyjnych stosuje się obie warstwy jednocześnie albo przenosi długą pracę do kolejki zewnętrznej.

---

## Problem naiwnej implementacji

Typowy kod:

```python
asyncio.create_task(process_video(task_id))
return {"task_id": task_id, "status": "accepted"}
```

Charakterystyka tej implementacji:

- fire-and-forget — brak rejestru aktywnych tasków
- brak fazy shutdown w `lifespan` po `yield`
- stan zadania w słowniku Python w RAM procesu
- brak zapisu postępu do trwałego magazynu

Gdy w trakcie przetwarzania przyjdzie sygnał zamknięcia procesu:

1. Uvicorn odbiera SIGINT lub SIGTERM.
2. Event loop się zamyka.
3. Korutyna w tle dostaje `asyncio.CancelledError` w trakcie `await asyncio.sleep()`.
4. Kod po `yield` w `lifespan` jest pusty — nikt nie czeka na dokończenie taska.
5. Słownik w pamięci RAM znika wraz z procesem.

Efekt dla klienta API:
- ostatni znany status: `running`, postęp np. 40%
- po restarcie serwera: brak zadania w systemie albo niespójny stan
- brak audytu, co stało się z danymi w trakcie deployu

To nie jest błąd frameworka. To brak warstwy shutdown i brak trwałości stanu zadania.

---

## Warstwa pierwsza: graceful shutdown

Graceful shutdown obsługuje planowane zatrzymanie procesu przy deployu i restarcie.

### Mechanizm lifespan w FastAPI

```python
@asynccontextmanager
async def lifespan(app):
    # startup — inicjalizacja store, task managera, processora
    yield
    # shutdown — oczekiwanie na taski w tle
    await task_manager.shutdown()
```

Kod przed `yield` wykonuje się przy starcie serwera. Kod po `yield` wykonuje się przy zatrzymaniu — to ostatni moment, w którym aplikacja kontroluje zakończenie pracy w tle.

### Mechanizm BackgroundTaskManager

Rejestr aktywnych tasków w `set[asyncio.Task]`:
- aplikacja wie, ile zadań jest w toku
- przy shutdown ustawia flagę `is_shutting_down = True`
- odrzuca nowe zadania kodem HTTP 503
- czeka na `shutdown_event` aż ostatni task się zakończy
- ma timeout — po przekroczeniu: `task.cancel()` i `asyncio.gather()`

### Efekt przy planowanym deployu

Klient uruchamia zadanie trwające około 60 sekund. W połowie przetwarzania przychodzi `SIGTERM` (lokalnie: Ctrl+C). W logach serwera:

```
graceful_shutdown_started   pending_tasks=1
checkpoint_saved            step=12
checkpoint_saved            step=13
video_processing_completed
graceful_shutdown_complete
```

Task kończy pracę, bo proces czekał na dokończenie przed wyjściem.

### Granica graceful shutdown

Graceful shutdown działa, gdy:
- orchestrator zapewnia wystarczający czas (`terminationGracePeriodSeconds`)
- zadanie mieści się w tym oknie czasowym

Przy zadaniu trwającym 60 minut i grace period 30 sekund w Kubernetes proces zakończy tylko tyle pracy, ile zdąży w tym czasie, a następnie dostanie `SIGKILL`. Dlatego przy długich zadaniach potrzebna jest druga warstwa — checkpointy — a przy bardzo długich: kolejka zewnętrzna i osobny worker.

---

## Warstwa druga: checkpoint i resume

Checkpoint i resume obsługują natychmiastowe zatrzymanie procesu, przy którym graceful shutdown nie ma szansy się wykonać.

### Zapis stanu po każdym etapie

Po ukończeniu każdego etapu przetwarzania aplikacja wywołuje `mark_step()` i zapisuje do SQLite:
- `task_id`
- `current_step`
- `status`
- `filename`
- `updated_at`
- `worker_id`

Plik `data/tasks.db` istnieje poza procesem aplikacji. Przeżywa restart procesu, `kill -9`, OOM kill i crash.

### Przebieg przy natychmiastowym zatrzymaniu

Stan w momencie zdarzenia:
- ostatni checkpoint zapisany na dysku — np. przy 40% postępu
- korutyna wykonuje kolejny etap — jest w trakcie `await asyncio.sleep()`
- przychodzi `kill -9` lub OOM kill
- kod po `yield` w lifespan się nie wykonuje
- proces kończy działanie natychmiast

Bez checkpointu: dane klienta są niespójne, zadanie przepada.
Z checkpointem w SQLite: na dysku jest ostatni ukończony etap, status zadania i identyfikator pliku. Stan biznesowy pozostaje.

### Resume po restarcie

Przy starcie nowego procesu:

1. `lifespan` łączy się z `data/tasks.db`.
2. `find_resumable()` wyszukuje zadania ze statusem `running` lub `interrupted`, gdzie `current_step < total_steps`.
3. `auto_resume_on_startup` wywołuje `resume()` dla każdego znalezionego zadania.
4. Processor uruchamia pętlę od `current_step + 1`.
5. Nowy proces ma nowy `worker_id` — w logach widać, że inną instancja przejęła zadanie.

W logach po restarcie:

```
orphaned_tasks_resumed_on_startup  count=1
video_processing_resumed           from_step=9  checkpoint_step=8
checkpoint_saved                     step=9
video_processing_completed
```

Klient odpytuje ten sam `task_id`. Status przechodzi do `completed`.

### Idempotentność etapów

Jeśli proces został zatrzymany w trakcie wykonywania etapu — checkpoint wskazuje poprzedni ukończony etap, a bieżący etap wykonuje się ponownie. W demo zakładamy, że powtórzenie etapu jest bezpieczne. W produkcji zawsze wymaga to sprawdzenia.

---

## Hierarchia rozwiązań w produkcji

**Zadania krótkie (sekundy do ~30 s)**
- przykłady: wysyłka maila, generowanie małego PDF, webhook
- rozwiązanie: graceful shutdown często wystarcza
- zadanie mieści się w grace period orchestratora

**Zadania średnie (minuty, w procesie API)**
- przykłady: raport batch, pipeline z etapami, przetwarzanie wsadowe
- rozwiązanie: checkpoint w bazie + graceful shutdown + resume
- to zakres tego demo

**Zadania długie (dziesiątki minut i więcej)**
- przykłady: transkodowanie 4K, trening ML, ETL godzinnego
- rozwiązanie: ten sam kontrakt API (`POST` + polling), pod spodem kolejka (SQS, RabbitMQ, Redis/ARQ, Celery) i osobny worker
- API tylko enqueueuje, worker przetwarza niezależnie od cyklu życia procesu API

**Zadania krytyczne z gwarancją dostarczenia**
- rozwiązanie: kolejka + idempotency key + dead letter queue + monitoring i alerty

---

## Ograniczenia demo

- Brak kolejki zewnętrznej (Redis, RabbitMQ, SQS).
- Jeden worker uvicorn — przy wielu replikach Kubernetes potrzebny distributed lock na resume, żeby dwa pody nie wznowiły tego samego zadania równolegle.
- SQLite zamiast MySQL / PostgreSQL — ten sam pattern, inny backend storage.
- Brak dead letter queue i retry z backoff.
- Etapy przetwarzania są idempotentne — na produkcji wymaga to osobnej strategii.

