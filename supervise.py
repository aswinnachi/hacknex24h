import subprocess
import sys
import time

def run():
    cmd = [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"]
    while True:
        try:
            print("[Supervisor] Launching uvicorn API server...", flush=True)
            p = subprocess.Popen(cmd)
            code = p.wait()
            print(f"[Supervisor] Server exited with code {code}. Restarting in 1s...", flush=True)
        except Exception as e:
            print(f"[Supervisor] Error: {e}", flush=True)
        time.sleep(1.0)

if __name__ == "__main__":
    run()
