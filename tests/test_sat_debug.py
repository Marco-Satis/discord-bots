"""Debug SAT CPU/RAM - teste jeden Schritt einzeln."""
import asyncio, sys, os, time
sys.path.insert(0, os.getcwd())
os.makedirs("logs", exist_ok=True)

async def debug():
    import psutil
    
    # 1. psutil Prozesssuche
    print("=== psutil Prozesssuche ===")
    for proc in psutil.process_iter(["name", "username", "pid", "create_time", "memory_info", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            user = proc.info.get("username") or ""
            if "FactoryServer" in name or "Satisfactory" in name:
                print(f"  Gefunden: PID={proc.info['pid']} Name={name} User={user}")
                mem_info = proc.info.get("memory_info")
                print(f"  memory_info: {mem_info}")
                try:
                    p = psutil.Process(proc.info["pid"])
                    p.cpu_percent()
                    time.sleep(0.1)
                    cpu = p.cpu_percent()
                    print(f"  cpu_percent: {cpu}")
                except psutil.AccessDenied as e:
                    print(f"  cpu_percent: AccessDenied ({e})")
                except psutil.NoSuchProcess:
                    print(f"  cpu_percent: NoSuchProcess")
        except (psutil.NoSuchProcess, psutil.AccessDenied) as e:
            if "FactoryServer" in str(proc):
                print(f"  AccessDenied fuer process_iter: {e}")

    # 2. systemd PID
    print("\n=== systemd PID ===")
    proc = await asyncio.create_subprocess_exec(
        "systemctl", "show", "--property=MainPID", "--value", "satisfactory.service",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    pid_str = stdout.decode().strip()
    print(f"  MainPID: {pid_str}")
    
    if pid_str and int(pid_str) > 0:
        pid = int(pid_str)
        # 3. /proc test
        print(f"\n=== /proc/{pid} Test ===")
        try:
            with open(f"/proc/{pid}/status", "r") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        print(f"  VmRSS: {line.strip()}")
                        break
                else:
                    print("  VmRSS: nicht gefunden")
        except PermissionError as e:
            print(f"  /proc/{pid}/status: PermissionError ({e})")
        except OSError as e:
            print(f"  /proc/{pid}/status: OSError ({e})")
        
        try:
            with open(f"/proc/{pid}/stat", "r") as f:
                stat_line = f.read()
            parts = stat_line.split(")")[-1].split()
            print(f"  /proc stat parts count: {len(parts)}")
            if len(parts) > 19:
                ticks = os.sysconf("SC_CLK_TCK")
                utime = int(parts[11])
                stime = int(parts[12])
                starttime = int(parts[19])
                print(f"  utime={utime} stime={stime} starttime={starttime} ticks={ticks}")
        except PermissionError as e:
            print(f"  /proc/{pid}/stat: PermissionError ({e})")
        except OSError as e:
            print(f"  /proc/{pid}/stat: OSError ({e})")

asyncio.run(debug())
