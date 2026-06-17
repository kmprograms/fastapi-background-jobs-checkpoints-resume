"""Scenariusz: hard kill + restart + auto-resume z checkpointu."""

import argparse
import asyncio
import sys

import httpx


async def poll_until(client: httpx.AsyncClient, base_url: str, task_id: str, min_step: int) -> None:
    while True:
        response = await client.get(f"{base_url}/api/v1/tasks/{task_id}")
        response.raise_for_status()
        data = response.json()
        step = data["current_step"]
        print(f"  krok {step}/{data['total_steps']}  status={data['status']}")

        if step >= min_step:
            print(f"\n💀 TERAZ ZABIJ PROCES NA SIŁĘ (nie Ctrl+C!):")
            print("   Windows: Zamknij terminal / taskkill /F /PID <pid>")
            print("   Linux:   kill -9 <pid>")
            print(f"\n   Ostatni checkpoint powinien być ~{step}. Potem uruchom serwer ponownie.\n")
            return

        if data["status"] == "completed":
            print("  Task skończony przedwcześnie — zacznij od nowa.")
            return

        await asyncio.sleep(1)


async def wait_for_resume(client: httpx.AsyncClient, base_url: str, task_id: str) -> None:
    print("⏳ Czekam na restart serwera i auto-resume...\n")
    last_step = -1

    while True:
        try:
            response = await client.get(f"{base_url}/api/v1/tasks/{task_id}")
            response.raise_for_status()
            data = response.json()
            step = data["current_step"]
            status = data["status"]
            worker = data.get("worker_id", "?")

            if step != last_step or status == "completed":
                print(
                    f"  [{status}] krok {step}/{data['total_steps']}  "
                    f"worker={worker}  checkpoint przeżył restart ✅"
                )
                last_step = step

            if status == "completed":
                print("\n🎉 SUKCES: długi job dokończony po hard kill + resume!")
                return

            await asyncio.sleep(1)
        except httpx.ConnectError:
            print("  ...serwer nie działa, czekam na restart...")
            await asyncio.sleep(2)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Demo hard kill + resume")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--filename", default="film_swieta_4k.mp4")
    parser.add_argument("--kill-at-step", type=int, default=8)
    args = parser.parse_args()

    print("\n🎬 Długi job + hard kill + resume z checkpointu")
    print(f"   Serwer: {args.base_url}\n")

    async with httpx.AsyncClient(timeout=30.0) as client:
        health = await client.get(f"{args.base_url}/api/v1/health")
        health.raise_for_status()
        h = health.json()
        print(f"   durable_store: {h['durable_store']}")
        print(f"   worker_id:     {h.get('worker_id')}\n")

        if not h["durable_store"]:
            print("❌ Serwer nie ma włączonego trwałego magazynu (SQLite).")
            sys.exit(1)

        response = await client.post(
            f"{args.base_url}/api/v1/tasks/video",
            json={"filename": args.filename},
        )
        response.raise_for_status()
        task_id = response.json()["id"]
        print(f"📤 Task: {task_id}")
        print(f"   Cel: zabij proces gdy dojdzie do kroku ~{args.kill_at_step}\n")

        await poll_until(client, args.base_url, task_id, args.kill_at_step)
        await wait_for_resume(client, args.base_url, task_id)


if __name__ == "__main__":
    asyncio.run(main())
