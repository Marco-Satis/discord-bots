"""Server-seitiger Test fuer Bot-Funktionen. Per SCP auf Server kopieren und ausfuehren."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())
os.makedirs("logs", exist_ok=True)

async def test_rcon():
    from modules.minecraft.rcon import MinecraftRCON
    from dotenv import load_dotenv
    load_dotenv("config/.env")
    pw = os.getenv("MC_BMC_RCON_PASSWORD", "")
    if not pw:
        print("RCON BMC5: SKIP (kein Passwort in .env)")
        return
    try:
        async with MinecraftRCON("localhost", 25575, pw, timeout=5.0) as rcon:
            r = await rcon.command("list")
            print(f"RCON BMC5: OK — {r[:80]}")
    except Exception as e:
        print(f"RCON BMC5: FAIL — {e}")

    # Vanilla RCON
    pw2 = os.getenv("MC_VANILLA_RCON_PASSWORD", "")
    if pw2:
        try:
            async with MinecraftRCON("localhost", 25576, pw2, timeout=5.0) as rcon:
                r = await rcon.command("list")
                print(f"RCON Vanilla: OK — {r[:80]}")
        except Exception as e:
            print(f"RCON Vanilla: FAIL — {e}")

def test_sat_port():
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(3)
    try:
        s.sendto(b"\xff\xff\xff\xff", ("localhost", 15777))
        print("SAT Query Port: erreichbar")
    except Exception:
        print("SAT Query Port: nicht erreichbar (Server evtl. offline)")
    finally:
        s.close()

def test_scheduler_cog():
    try:
        from cogs.scheduler_cog import SchedulerCog
        print("Scheduler Cog Import: OK")
    except Exception as e:
        print(f"Scheduler Cog Import: FAIL — {e}")

if __name__ == "__main__":
    asyncio.run(test_rcon())
    test_sat_port()
    test_scheduler_cog()
