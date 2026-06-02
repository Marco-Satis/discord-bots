"""Server-seitiger Test fuer Auto-Update-System."""
import sys, os, asyncio
sys.path.insert(0, os.getcwd())
os.makedirs("logs", exist_ok=True)
os.environ.setdefault("RUNNING_TEST", "1")

def test_imports():
    try:
        from modules.minecraft.update_manager import UpdateManager
        from modules.minecraft.modpack_updater import ModpackUpdater
        from modules.minecraft.file_manager import FileManager
        from modules.minecraft.mc_countdown import MCCountdownTimer
        from modules.minecraft.neoforge_updater import NeoForgeUpdater
        from modules.monitoring.update_checker import UpdateChecker
        print("Update-Module Import: OK")
    except Exception as e:
        print(f"Update-Module Import: FAIL — {e}")

async def test_curseforge():
    import aiohttp
    from dotenv import load_dotenv
    load_dotenv("config/.env")
    key = os.getenv("CURSEFORGE_API_KEY", "")
    if not key:
        print("CurseForge API: SKIP (Kein API Key)")
        return
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://api.curseforge.com/v1/games", headers={"x-api-key": key}) as r:
                print(f"CurseForge API: {r.status} ({'OK' if r.status == 200 else 'FAIL'})")
    except Exception as e:
        print(f"CurseForge API: FAIL — {e}")

def test_db():
    import sqlite3
    db = sqlite3.connect("data/botdata.db")
    ver = db.execute("PRAGMA user_version").fetchone()[0]
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('modpack_updates','server_versions')").fetchall()]
    print(f"DB Version: {ver} (erwartet 4)")
    print(f"Update-Tabellen: {tables}")
    for t in tables:
        cols = [r[1] for r in db.execute(f"PRAGMA table_info({t})").fetchall()]
        print(f"  {t} Spalten: {cols}")
    db.close()

def test_staging():
    staging = "/home/minecraft/.update_staging"
    exists = os.path.isdir(staging)
    print(f"Staging Dir: {'OK' if exists else 'FEHLT'} ({staging})")

if __name__ == "__main__":
    test_imports()
    asyncio.run(test_curseforge())
    test_db()
    test_staging()
