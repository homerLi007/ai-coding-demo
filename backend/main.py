from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .models import RunRequest
from .agent_runner import run_agent_stream

app = FastAPI(title="Multi-Agent API", version="0.1.0")

# 允许 React 开发服务器跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.post("/api/run")
async def run(req: RunRequest):
    """触发多智能体流程，以 SSE 流式返回进度。"""
    return StreamingResponse(
        run_agent_stream(req.model_dump()),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",   # 禁用 nginx 缓冲
        },
    )

# ── 托管前端静态文件（仅在生产镜像中存在）──
from pathlib import Path
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"
if FRONTEND_DIST.exists():
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/")
    def serve_index():
        return FileResponse(FRONTEND_DIST / "index.html")

    @app.get("/{path:path}")
    def serve_spa(path: str):
        # 让 SPA 路由回退到 index.html
        file = FRONTEND_DIST / path
        if file.is_file():
            return FileResponse(file)
        return FileResponse(FRONTEND_DIST / "index.html")