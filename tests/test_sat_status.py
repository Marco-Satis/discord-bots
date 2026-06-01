"""Test SAT CPU/RAM Status nach Fix."""
import asyncio, sys, os
sys.path.insert(0, os.getcwd())
os.makedirs("logs", exist_ok=True)
from modules.satisfactory.server import SatisfactoryServer

async def main():
    s = SatisfactoryServer()
    status = await s.get_status()
    print(f"Running: {status['running']}")
    print(f"PID: {status['pid']}")
    print(f"CPU: {status['cpu_percent']}%")
    print(f"RAM: {status['memory_mb']} MB")
    print(f"Uptime: {status['uptime']}s")

asyncio.run(main())
