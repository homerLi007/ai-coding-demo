import httpx, os
import json

EXECUTOR_URL = os.environ.get("EXECUTOR_URL", "http://localhost:8081")

async def run_sync(command, files, workdir="/workspace", timeout=300, network_disabled=True):
    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        resp = await client.post(
            f"{EXECUTOR_URL}/v1/execute",
            json={
                "command": command,
                "files": files,
                "workdir": workdir,
                "timeout": timeout,
                "network_disabled": network_disabled,
                "image": "sandbox-maven-node:latest",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        passed = data["exit_code"] == 0
        output = (data.get("stdout") or "") + "\n" + (data.get("stderr") or "")
        return passed, output


async def run_stream(
        command: list[str],
        files: dict[str, str],
        workdir: str = "/workspace",
        timeout: int = 300,
        network_disabled: bool = True,
        image: str = "sandbox-maven-node:latest",
        on_line=None,          # 可选：每收到一行日志时回调，用于实时打印
) -> tuple[bool, str]:
    lines: list[str] = []
    exit_code = -1

    async with httpx.AsyncClient(timeout=timeout + 30) as client:
        async with client.stream(
                "POST",
                f"{EXECUTOR_URL}/v1/execute/stream",
                json={
                    "command": command,
                    "files": files,
                    "workdir": workdir,
                    "timeout": timeout,
                    "network_disabled": network_disabled,
                    "image": image,
                },
        ) as resp:
            resp.raise_for_status()
            async for raw in resp.aiter_lines():
                if not raw:
                    continue
                event = json.loads(raw)
                if "line" in event:
                    line = event["line"]
                    lines.append(line)
                    if on_line:
                        on_line(line)
                    else:
                        print(f"[sandbox] {line.rstrip()}")
                elif "exit_code" in event:
                    exit_code = event["exit_code"]

    return exit_code == 0, "\n".join(lines)