import hashlib
import uuid
from typing import AsyncGenerator

from supervisor_multi_agent import _derive_thread_id


def _build_initial_inputs(task: str) -> dict:
    return {
        "task": task,
        "messages": [],
        "next_agent": "",
        "code": "",
        "files": {},
        "security_report": "",
        "needs_fix": False,
        "fix_attempts": 0,
        "documentation": "",
        "iteration_count": 0,
        "github_owner": "homerli007-ai",
        "github_repo": "springbootapplication",
        "github_base_branch": "main",
        "github_code_path": "",
        "github_pr_url": "",
        "test_report": "",
        "test_passed": False,
        "test_attempts": 0,
        "e2e_report": "",
        "e2e_passed": False,
        "e2e_attempts": 0,
        "generated_repo": "",
        "generated_repo_url": "",
        "app_url": "",
        "app_nodeport": 0,
    }


async def run_agent_stream(
        task: str, task_id: str | None = None
) -> AsyncGenerator[dict, None]:
    # 延迟导入，避免与 main.py 循环导入
    from backend.main import _holder

    # 1. 取 app（必须在任何使用 agent_app 之前）
    agent_app = _holder.get("app")
    if agent_app is None:
        yield {"type": "error", "message": "agent app not initialized"}
        return

    # 2. 派生 thread_id
    if not task_id:
        task_id = f"adhoc-{uuid.uuid4().hex[:12]}"
    thread_id = _derive_thread_id(task_id)
    config = {"configurable": {"thread_id": thread_id}}

    # 3. 异步读取 checkpoint 状态（注意是 aget_state，不是 get_state）
    snapshot = await agent_app.aget_state(config)
    is_resume = bool(snapshot and snapshot.values)
    inputs = {} if is_resume else _build_initial_inputs(task)

    yield {
        "type": "meta",
        "task_id": task_id,
        "thread_id": thread_id,
        "mode": "resume" if is_resume else "new",
    }

    # 4. 异步流式执行
    async for event in agent_app.astream(inputs, config=config, stream_mode="values"):
        yield {
            "type": "state",
            "next_agent": event.get("next_agent"),
            "iteration": event.get("iteration_count"),
            "has_files": bool(event.get("files")),
            "test_passed": event.get("test_passed"),
            "e2e_passed": event.get("e2e_passed"),
            "app_url": event.get("app_url"),
            "github_pr_url": event.get("github_pr_url"),
        }

    yield {"type": "done", "thread_id": thread_id}