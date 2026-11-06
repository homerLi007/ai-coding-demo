import threading

import docker
import tempfile
import os
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import json

app = FastAPI()
client = docker.from_env()


class ExecuteRequest(BaseModel):
    command: list[str]
    files: dict[str, str]
    workdir: str = "/workspace"
    timeout: int = 300
    network_disabled: bool = True
    image: str = "sandbox-maven-node:latest"


def _write_files(files: dict, base: str):
    for path, content in files.items():
        full = os.path.join(base, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content or "")


@app.post("/v1/execute")
def execute(req: ExecuteRequest):
    with tempfile.TemporaryDirectory() as tmp:
        _write_files(req.files, tmp)
        container = client.containers.create(
            image=req.image,
            command=req.command,
            working_dir=req.workdir,
            volumes={tmp: {"bind": "/workspace", "mode": "rw"}},
            network_disabled=req.network_disabled,
            read_only=True,
            mem_limit="1g",
            pids_limit=128,
            cpu_quota=50000,
            cpu_period=100000,
            cap_drop=["ALL"],
            security_opt=["no-new-privileges:true"],
            user="65532:65532",
            detach=True,
        )
        try:
            container.start()
            status = container.wait(timeout=req.timeout)["StatusCode"]
            stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
            return {"exit_code": status, "stdout": stdout, "stderr": stderr}
        finally:
            container.remove(force=True)

@app.post("/v1/execute/stream")
def execute_stream(req: ExecuteRequest):
    def gen():
        with tempfile.TemporaryDirectory() as tmp:        # ← 在 gen 内部
            _write_files(req.files, tmp)
            container = client.containers.create(          # ← 补上创建逻辑
                image=req.image,
                command=req.command,
                working_dir=req.workdir,
                volumes={tmp: {"bind": "/workspace", "mode": "rw"}},
                network_disabled=req.network_disabled,
                read_only=True,
                mem_limit="1g",
                pids_limit=128,
                cpu_quota=50000,
                cpu_period=100000,
                cap_drop=["ALL"],
                security_opt=["no-new-privileges:true"],
                user="65532:65532",
                detach=True,
            )
            timer = threading.Timer(req.timeout, lambda: container.kill())
            timer.start()
            try:
                container.start()
                # 先迭代日志流，容器退出时迭代器自然结束
                for line in container.logs(stream=True, follow=True):
                    yield json.dumps({
                        "stream": "log",
                        "line": line.decode(errors="replace"),
                    }) + "\n"
                # 日志流结束后，容器已退出，wait 立即返回
                status = container.wait(timeout=req.timeout)["StatusCode"]
                yield json.dumps({"exit_code": status}) + "\n"
            finally:
                timer.cancel()
                container.remove(force=True)               # 先删容器
            # with 退出时删 tmp 目录，此时容器已不存在，安全

    return StreamingResponse(gen(), media_type="application/x-ndjson")