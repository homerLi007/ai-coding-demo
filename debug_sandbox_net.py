# debug_sandbox_net.py
import httpx

resp = httpx.post(
    "http://localhost:8081/v1/execute",
    json={
        "command": [
            "bash", "-lc",
            "curl -sS -m 3 http://169.254.169.254 && echo REACHABLE || echo BLOCKED"
        ],
        "files": {},
        "workdir": "/workspace",
        "timeout": 30,
        "network_disabled": True,
    },
    timeout=60,
)
print("exit_code:", resp.json()["exit_code"])
print("stdout:", resp.json()["stdout"])
print("stderr:", resp.json()["stderr"])