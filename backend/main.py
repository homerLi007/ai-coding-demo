import json
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from supervisor_multi_agent import workflow, create_checkpointer
from backend.agent_runner import run_agent_stream


_holder = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    checkpointer, pool = await create_checkpointer()
    _holder["app"] = workflow.compile(checkpointer=checkpointer)
    _holder["pool"] = pool
    yield
    await pool.close()


app = FastAPI(lifespan=lifespan)          # ← 模块级


class RunRequest(BaseModel):               # ← 模块级
    task: str
    task_id: str | None = None


@app.post("/api/run")
async def run_agent(body: RunRequest):
    async def gen():
        async for chunk in run_agent_stream(body.task, body.task_id):
            event_type = chunk.get("type", "message")
            payload = json.dumps(chunk, ensure_ascii=False)
            yield f"event: {event_type}\ndata: {payload}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")