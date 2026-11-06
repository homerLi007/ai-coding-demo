# debug_stream.py
import time
import httpx

start = time.time()

with httpx.stream(
        "POST",
        "http://localhost:8081/v1/execute/stream",
        json={
            "command": ["bash", "-lc",
                        "sleep 3; echo A; sleep 3; echo B; sleep 3; echo C"],
            "files": {},
            "workdir": "/workspace",
            "timeout": 30,
            "network_disabled": True,
        },
        timeout=60,
) as resp:
    print(f"status: {resp.status_code}")
    for raw in resp.iter_lines():
        if not raw:
            continue
        print(f"[+{time.time() - start:5.1f}s] {raw}")