import threading

import docker
import tempfile
import os
from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.responses import StreamingResponse
import json
import io
import tarfile

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
            tmpfs={"/workspace": "rw,size=1g"},
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
            if req.files:
               container.put_archive("/workspace", _make_tar(req.files))
            status = container.wait(timeout=req.timeout)["StatusCode"]
            stdout = container.logs(stdout=True, stderr=False).decode(errors="replace")
            stderr = container.logs(stdout=False, stderr=True).decode(errors="replace")
            return {"exit_code": status, "stdout": stdout, "stderr": stderr}
        finally:
            container.remove(force=True)

@app.post("/v1/execute/stream")
def execute_stream(req: ExecuteRequest):
    def gen():
        container = client.containers.create(
            image=req.image,
            command=req.command,
            working_dir=req.workdir,
            network_disabled=req.network_disabled,
            # 去掉 read_only=True 和 tmpfs
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
            # 关键：put_archive 在 start 之前执行，容器处于 created 状态，没有竞态
            if req.files:
                container.put_archive("/workspace", _make_tar(req.files))

            container.start()

            for line in container.logs(stream=True, follow=True):
                yield json.dumps({
                    "stream": "log",
                    "line": line.decode(errors="replace"),
                }) + "\n"

            status = container.wait(timeout=req.timeout)["StatusCode"]
            yield json.dumps({"exit_code": status}) + "\n"
        finally:
            container.remove(force=True)

    return StreamingResponse(gen(), media_type="application/x-ndjson")

def _make_tar(files: dict) -> io.BytesIO:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path, content in files.items():
            data = (content or "").encode("utf-8")
            info = tarfile.TarInfo(name=path)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    buf.seek(0)
    return buf