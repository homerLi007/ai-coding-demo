import json
from typing import Any, AsyncGenerator, Dict

from supervisor_multi_agent import app as agent_app


async def run_agent_stream(req: Dict[str, Any]) -> AsyncGenerator[str, None]:
    """执行多智能体流程，以 SSE 格式流式返回每个节点的更新。"""
    initial_inputs = {
        "task": req["task"],
        "messages": [],
        "next_agent": "",
        "code": "",
        "files": {},                 # ← 新增：多文件项目
        "security_report": "",
        "needs_fix": False,
        "fix_attempts": 0,
        "documentation": "",
        "github_owner": req["github_owner"],
        "github_repo": req["github_repo"],
        "github_base_branch": req.get("github_base_branch", "main"),
        "github_code_path": "",
        "github_pr_url": "",
        "iteration_count": 0,
    }

    try:
        async for chunk in agent_app.astream(initial_inputs, stream_mode="updates"):
            for node_name, update in chunk.items():
                if not isinstance(update, dict):
                    continue

                # ── 1. 创建事件对象 ──
                event: Dict[str, Any] = {"node": node_name}

                # ── 2. 逐个字段填充（这里就是你要加的位置，和其他 if 并列）──
                if "iteration_count" in update:
                    event["iteration"] = update["iteration_count"]
                if "next_agent" in update:
                    event["next_agent"] = update["next_agent"]
                if "needs_fix" in update:
                    event["needs_fix"] = update["needs_fix"]
                if "fix_attempts" in update:
                    event["fix_attempts"] = update["fix_attempts"]

                # 👇👇👇 加在这里：紧跟在 fix_attempts 之后，github_pr_url 之前 👇👇👇
                if "files" in update and update["files"]:
                    event["file_count"] = len(update["files"])
                    event["file_paths"] = sorted(update["files"].keys())
                # 👆👆👆 加到这里 👆👆👆

                if update.get("github_pr_url"):
                    event["pr_url"] = update["github_pr_url"]
                    event["final"] = True

                # ── 3. 发送 SSE 事件 ──
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

        yield "event: done\ndata: {}\n\n"

    except Exception as e:
        error_event = {"type": type(e).__name__, "message": str(e)}
        yield f"event: error\ndata: {json.dumps(error_event, ensure_ascii=False)}\n\n"