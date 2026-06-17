"""
Uruchamia task wideo i co sekundę pokazuje postęp.
"""

import argparse
import asyncio
import sys

import httpx


async def poll_status(client: httpx.AsyncClient, base_url: str, task_id: str) -> None:
    while True:
        response = await client.get(f"{base_url}/api/v1/tasks/{task_id}")
        response.raise_for_status()
        data = response.json()

        status = data["status"]
        step = data["current_step"]
        total = data["total_steps"]
        percent = data["progress_percent"]

        bar_filled = int(percent / 5)
        bar = "█" * bar_filled + "░" * (20 - bar_filled)
        print(f"\r  [{bar}] {percent:5.1f}%  krok {step}/{total}  status={status}  ", end="", flush=True)

        if status in ("completed", "failed", "interrupted"):
            print()
            if status == "completed":
                print("  ✅ Task zakończony bezpiecznie!")
            elif status == "interrupted":
                print("  ⚠️ Task przerwany — checkpoint zachowany, możliwy resume po restarcie")
            else:
                print(f"  ❌ Task failed: {data.get('error')}")
            return

        await asyncio.sleep(1)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demo client — fastapi-background-jobs-checkpoints-resume"
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="URL serwera",
    )
    parser.add_argument(
        "--filename",
        default="wedding_clip_4k.mp4",
        help="Nazwa pliku wideo do symulacji",
    )
    args = parser.parse_args()

    print(f"\n🎬 Demo: fastapi-background-jobs-checkpoints-resume")
    print(f"   Serwer: {args.base_url}")
    print(f"   Plik:   {args.filename}\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        health = await client.get(f"{args.base_url}/api/v1/health")
        health.raise_for_status()
        h = health.json()
        print(f"   Worker: {h.get('worker_id')}  |  checkpointy: {h['durable_store']}\n")

        print("📤 Wysyłam task przetwarzania wideo...")
        response = await client.post(
            f"{args.base_url}/api/v1/tasks/video",
            json={"filename": args.filename},
        )
        response.raise_for_status()
        task = response.json()
        task_id = task["id"]
        print(f"   Task ID: {task_id}")
        print(f"   Kroki:   {task['total_steps']} × ~2s = ~{task['total_steps'] * 2}s\n")
        print("⏳ Śledzę postęp (teraz zrób Ctrl+C na serwerze w połowie!):\n")

        try:
            await poll_status(client, args.base_url, task_id)
        except httpx.ConnectError:
            print("\n  💀 Połączenie zerwane — serwer umarł, task prawdopodobnie przerwany!")
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
