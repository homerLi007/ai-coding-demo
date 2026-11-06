# debug_put_archive.py
import time, httpx

with httpx.stream("POST", "http://localhost:8081/v1/execute/stream",
                  json={
                      "command": ["bash", "-lc", "ls -R /workspace && cat /workspace/backend/pom.xml | head -5"],
                      "files": {"backend/pom.xml": "<project>hello</project>"},
                      "workdir": "/workspace",
                      "timeout": 30,
                      "network_disabled": True,
                  }, timeout=60) as resp:
    for line in resp.iter_lines():
        print(line)