import os
import json
from typing import Annotated, TypedDict, Sequence, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
import operator
from dotenv import load_dotenv
from github import Github, Auth, GithubException
from datetime import datetime
import re

load_dotenv()

import os

_ALLOWED_EXTS = {
    ".java", ".kt", ".gradle", ".xml", ".properties", ".yml", ".yaml",
    ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".css",
    ".py", ".md", ".txt", ".gitignore",
}

def sanitize_filename(name: str) -> str:
    """把 LLM 返回的相对路径清洗成安全的、仓库内可用的路径。
    - 拒绝绝对路径、..、NUL
    - 保留子目录（Spring Boot 的 src/main/java/... 需要保留）
    - 保留合法扩展名；无扩展名的按 .txt 处理
    """
    if not name or not isinstance(name, str):
        return "README.md"

    # 统一分隔符，转成 POSIX 风格
    name = name.replace("\\", "/").strip()
    if name.startswith("/"):
        name = name.lstrip("/")

    # 过滤危险段
    parts = []
    for p in name.split("/"):
        p = p.strip()
        if p in ("", ".", ".."):
            continue
        if "\x00" in p:
            continue
        # 过滤非法字符（保留字母数字、_-、点）
        p = re.sub(r"[^A-Za-z0-9_\-.]", "_", p)
        if p:
            parts.append(p)

    if not parts:
        return "README.md"

    # 检查扩展名
    base = parts[-1]
    root, ext = os.path.splitext(base)
    if ext.lower() not in _ALLOWED_EXTS:
        # 无合法扩展名 → 当作 .txt
        parts[-1] = base + ".txt"

    return "/".join(parts)

# ==========================================
# 1. Define Advanced State
# ==========================================
class SupervisorState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    task: str
    next_agent: str          # Managed by supervisor: "coder", "security", "writer", or "FINISH"
    code: str
    files: dict[str, str]
    security_report: str
    needs_fix: bool
    fix_attempts: int
    documentation: str
    iteration_count: int
    github_owner: str
    github_repo: str
    github_base_branch: str
    github_code_path: str
    github_pr_url: str

def sanitize_py_filename(name: str) -> str:
    """把 LLM 返回的文件名清洗成合法的、单层的 .py 文件名。"""
    if not name:
        return "solution.py"

    # 只取最后一段，防止 ../ 路径穿越
    name = name.replace("\\", "/").split("/")[-1].strip()

    # 去掉扩展名，统一小写、空格转下划线，只保留字母数字下划线
    name = re.sub(r"\.py$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"[^A-Za-z0-9_]", "", name)

    if not name:
        return "solution.py"

    # Python 标识符不能以数字开头
    if name[0].isdigit():
        name = f"module_{name}"

    return f"{name.lower()}.py"

def _extract_top_issues(report: str, max_issues: int = 3) -> list[str]:
    """只提取 CRITICAL / HIGH 级别的 [FIXABLE] 条目。"""
    order = {"CRITICAL": 0, "HIGH": 1}
    blocks = re.split(r"(?m)^###\s+", report)
    parsed = []
    for b in blocks[1:]:
        if "[INHERENT]" in b or "[INFO]" in b:
            continue
        if "[FIXABLE]" not in b:
            continue
        m = re.search(r"\*\*Severity:\*\*\s*(CRITICAL|HIGH|MEDIUM|LOW)", b, re.I)
        sev = m.group(1).upper() if m else "MEDIUM"
        if sev not in ("CRITICAL", "HIGH"):
            continue                                  # ← 只保留 CRITICAL/HIGH
        body = b.strip()
        parsed.append((order.get(sev, 99), f"### {body[:1200]}"))

    parsed.sort(key=lambda x: x[0])
    return [p[1] for p in parsed[:max_issues]]

# ==========================================
# 2. Initialize DeepSeek LLM Client
# ==========================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=DEEPSEEK_API_KEY,
    temperature=0.3
)

# 修复专用低温实例（避免 llm.bind 的兼容问题）
llm_fix = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=DEEPSEEK_API_KEY,
    temperature=0.1,
)

# ==========================================
# 3. Define Specialized Worker Nodes
# ==========================================

def coder_node(state: SupervisorState):
    print("\n💻 [Coder Agent]: Generating Spring Boot + React project...")
    task = state["task"]
    feedback = state.get("security_report", "")
    is_fixing = state.get("needs_fix", False)
    previous_files: dict[str, str] = state.get("files", {}) or {}
    fix_attempts = state.get("fix_attempts", 0)

    try:
        if is_fixing and feedback and previous_files:
            print(f"🔁 收到 security 反馈 (第 {fix_attempts + 1} 次修复)...")
            issues = _extract_top_issues(feedback, max_issues=3)
            issues_block = "\n\n".join(issues) if issues else feedback[:3000]
            files_block = _format_files_for_prompt(previous_files)

            prompt = FIX_PROMPT.format(
                task=task, files_block=files_block, issues_block=issues_block
            )
            response = llm_fix.invoke([HumanMessage(content=prompt)])
        else:
            prompt = INITIAL_PROMPT.format(task=task)
            response = llm.invoke([HumanMessage(content=prompt)])

        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            payload = json.loads(content)
            raw_files = payload.get("files", {})
            changes = payload.get("changes", "")
            notes = payload.get("notes", "")
        except json.JSONDecodeError:
            print("⚠️ Coder 未返回合法 JSON，尝试从 markdown 兜底解析")
            # 简单兜底：把整段当单文件
            raw_files = {"README.md": content}
            changes = "(fallback)"
            notes = ""

        # 清洗路径 + 合并
        cleaned: dict[str, str] = {}
        for raw_path, file_content in raw_files.items():
            if not isinstance(file_content, str):
                continue
            path = sanitize_filename(raw_path)
            cleaned[path] = file_content

        if not cleaned:
            raise ValueError("LLM 返回的 files 为空")

        if is_fixing:
            merged = dict(previous_files)
            merged.update(cleaned)          # 只覆盖返回的文件
        else:
            merged = cleaned

        # 主文件（用于 supervisor 判断"是否已生成"，也用于 code 字段兼容）
        primary = next(
            (p for p in merged if p.endswith(("DemoApplication.java", "pom.xml"))),
            next(iter(merged)),
        )
        primary_content = merged[primary]

        print(f"📄 生成 {len(merged)} 个文件:")
        for p in sorted(merged.keys()):
            print(f"   - {p} ({len(merged[p])} 字符)")
        print(f"📝 改动说明: {changes}")
        if notes:
            print(f"⚠️ 已知局限: {notes}")

        # 检测"无变化"
        if is_fixing and cleaned == {k: previous_files.get(k) for k in cleaned}:
            print("⚠️ 修复后文件无变化，强制放行")

        return {
            "files": merged,
            "code": primary_content,          # 兼容旧字段
            "github_code_path": primary,
            "security_report": "",
            "needs_fix": False,
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [AIMessage(content=f"Coder: {len(merged)} files, {changes}")],
        }

    except Exception as exc:
        print(f"❌ Coder 异常: {type(exc).__name__}: {exc}")
        import traceback; traceback.print_exc()
        return {
            "files": previous_files,
            "code": state.get("code", ""),
            "github_code_path": state.get("github_code_path", ""),
            "security_report": "",
            "needs_fix": False,
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [AIMessage(content=f"Coder error: {exc}")],
        }

        return {
            "code": code,
            "github_code_path": filename,
            "security_report": "",
            "needs_fix": False,              # ← 清空，重新审计
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [AIMessage(content=f"Coder produced {filename}: {changes}")],
        }

    except Exception as exc:
        # 任何异常都不能让 coder 死循环——打印出来 + 强制放行
        print(f"❌ Coder 异常: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return {
            "code": previous_code,           # 保留旧代码
            "github_code_path": previous_filename or "solution.py",
            "security_report": "",
            "needs_fix": False,              # 强制放行，交给 writer/github
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [AIMessage(content=f"Coder error: {exc}")],
        }

def security_node(state: SupervisorState):
    print("\n🔒 [Security Auditor]: Auditing Spring Boot + React project...")
    files = state.get("files", {}) or {}
    files_block = _format_files_for_prompt(files, max_chars=12000)

    prompt = f"""Audit this **Spring Boot + React** project for security issues:

{files_block}

Output format (follow EXACTLY):
Line 1: SEVERITY: <CRITICAL|HIGH|MEDIUM|LOW|NONE>
Line 2: (blank line)
Line 3+: Full markdown audit report.

Severity rules (STRICT):
- CRITICAL : RCE, auth bypass, data loss — BLOCKS shipping
- HIGH     : exploitable vulnerability under realistic conditions — BLOCKS shipping
- MEDIUM   : real weakness with preconditions — reported only, does NOT block
- LOW      : best-practice deviation — reported only, does NOT block
- NONE     : no actionable issue

ONLY CRITICAL and HIGH trigger a code rewrite. Do not escalate MEDIUM to HIGH.

What to look for in Spring Boot:
- Command injection (Runtime.exec with user input)
- SQL injection (JDBC string concatenation) — only if code has SQL
- Path traversal (new File(userInput))
- Deserialization (ObjectInputStream on untrusted data)
- Hardcoded secrets in application.properties

What to look for in React:
- dangerouslySetInnerHTML with untrusted content
- Direct DOM manipulation with user input
- Hardcoded API keys in source

Do NOT report: missing auth (out of scope), missing HTTPS config, missing CORS headers.
Only report issues that can be fixed by changing the code.
For every finding, prefix with [FIXABLE] / [INHERENT] / [INFO].
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content.strip()

    m = re.search(r"SEVERITY\s*:\s*(CRITICAL|HIGH|MEDIUM|LOW|NONE)", content, re.I)
    severity = m.group(1).upper() if m else "MEDIUM"

    needs_fix = severity in ("CRITICAL", "HIGH")
    fix_attempts = state.get("fix_attempts", 0)
    BLOCKING_LIMIT = 10
    if needs_fix and fix_attempts >= BLOCKING_LIMIT:
        print(f"🚨 {severity} 已修 {fix_attempts} 次仍未通过，强制放行")
        needs_fix = False

    print(f"🛡️ Severity: {severity}, needs_fix: {needs_fix}, fix_attempts: {fix_attempts}")
    print(f"🛡️ Security Report:\n{content}")

    return {
        "security_report": content,
        "needs_fix": needs_fix,
        "messages": [AIMessage(content=f"Security Audit ({severity})")],
    }

def writer_node(state: SupervisorState):
    print("\n📝 [Doc Writer]: Generating README...")
    files = state.get("files", {}) or {}
    files_block = _format_files_for_prompt(files, max_chars=10000)

    prompt = f"""Write a professional README.md for this Spring Boot + React project.

## Project files
{files_block}

## Requirements
- Start with `# <project name>` (derive from pom.xml artifactId or task)
- `## Stack` — one line: Spring Boot 3.x / Java 17 / React 18 / Vite
- `## Layout` — tree of the top-level directories
- `## How to run` — backend: `cd backend && mvn spring-boot:run`; frontend: `cd frontend && npm install && npm run dev`
- `## API` — list endpoints from the controller
- `## Notes` — any known limitations

Return ONLY the raw README.md content. Do NOT wrap in a code fence.
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    doc = response.content.strip()

    # 兜底：剥掉可能的代码围栏
    if doc.startswith("```"):
        doc = re.sub(r"^```(?:markdown|md)?\s*", "", doc)
        doc = re.sub(r"\s*```$", "", doc)

    print("📚 README generated.")
    return {
        "documentation": doc,
        "messages": [AIMessage(content="Documentation generated")],
    }

# ==========================================
# 4. The Supervisor Node (The Router Brain)
# ==========================================
def supervisor_node(state: SupervisorState):
    print("\n👑 [Supervisor / Tech Lead]: Evaluating project status...")

    if not state.get("code"):
        next_agent = "coder"
    elif state.get("needs_fix"):
        next_agent = "coder"
    elif not state.get("security_report"):
        next_agent = "security"
    elif not state.get("documentation"):
        next_agent = "writer"
    elif not state.get("github_pr_url"):
        next_agent = "github"
    else:
        next_agent = "FINISH"

    iteration = state.get("iteration_count", 0) + 1
    if iteration > 50:
        next_agent = "FINISH"

    print(f"🎯 Supervisor Decision -> Next Worker: {next_agent}")
    return {"next_agent": next_agent, "iteration_count": iteration}



def github_commit_node(state: SupervisorState):
    print("\n🐙 [GitHub Agent]: 提交项目到仓库...")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN 未设置")

    g = Github(auth=Auth.Token(token))
    repo = g.get_repo(f"{state['github_owner']}/{state['github_repo']}")
    base = repo.get_branch(state["github_base_branch"])

    new_branch = f"ai-agent/{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    try:
        repo.create_git_ref(ref=f"refs/heads/{new_branch}", sha=base.commit.sha)
    except GithubException as e:
        if e.status != 422:
            raise

    # 组装所有要提交的文件
    files = dict(state.get("files", {}) or {})
    files["README.md"] = state.get("documentation", "") or "# Generated by AI Agent"

    for path, content in files.items():
        try:
            existing = repo.get_contents(path, ref=new_branch)
            repo.update_file(
                path=path, message=f"AI Agent: update {path}",
                content=content or "", sha=existing.sha, branch=new_branch,
            )
        except GithubException:
            repo.create_file(
                path=path, message=f"AI Agent: add {path}",
                content=content or "", branch=new_branch,
            )
        print(f"   📄 已提交 {path}")

    try:
        pr = repo.create_pull(
            title=f"AI Agent: {state['task'][:60]}",
            body=f"**任务**: {state['task']}\n\n**安全审计**:\n{state.get('security_report', 'N/A')}",
            head=new_branch, base=state["github_base_branch"],
        )
        pr_url = pr.html_url
    except GithubException as e:
        if e.status == 422:
            pr_url = next(
                (p.html_url for p in repo.get_pulls(
                    state="open", head=f"{repo.owner.login}:{new_branch}")), "")
        else:
            raise

    print(f"✅ PR: {pr_url}")
    return {"github_pr_url": pr_url, "messages": [AIMessage(content=f"PR: {pr_url}")]}

# ==========================================
# 5. Define Conditional Routing Edge
# ==========================================
def route_next(state: SupervisorState) -> Literal["coder", "security", "writer", "github", "END"]:
    next_agent = state["next_agent"]
    if next_agent in ("coder", "security", "writer", "github"):
        return next_agent
    return "END"

# ==========================================
# 6. Assemble the Supervisor Graph
# ==========================================
workflow = StateGraph(SupervisorState)

# Add all worker nodes and supervisor node
workflow.add_node("supervisor", supervisor_node)
workflow.add_node("coder", coder_node)
workflow.add_node("security", security_node)
workflow.add_node("writer", writer_node)
workflow.add_node("github", github_commit_node)

# Set entry point to supervisor
workflow.set_entry_point("supervisor")

# From workers, always return back to the supervisor to coordinate the next step
workflow.add_edge("coder", "supervisor")
workflow.add_edge("security", "supervisor")
workflow.add_edge("writer", "supervisor")
workflow.add_edge("github", "supervisor")

# From supervisor, conditionally route to workers or end
workflow.add_conditional_edges(
    "supervisor",
    route_next,
    {
        "coder": "coder",
        "security": "security",
        "writer": "writer",
        "github": "github",
        "END": END
    }
)

app = workflow.compile()

# ==========================================
# 7. Run the Supervisor Multi-Agent System
# ==========================================
if __name__ == "__main__":
    print("🚀 Supervisor Multi-Agent System Started...")

    initial_inputs = {
        "task": (
            "Create a function that takes a list of file paths and deletes them. "
            "Security requirements (and ONLY these): "
            "(a) refuse symlinks (the named entry, never a symlink target); "
            "(b) when allowed_root is given, refuse paths outside it; "
            "(c) refuse non-regular files (dirs, FIFOs, devices). "
            "EXPLICIT NON-GOALS (out of scope, do NOT report as findings): "
            "TOCTOU against attackers with write access to the parent directory; "
            "Windows alternate data streams; 8.3 short names; hard links; "
            "secure erasure of file contents; permission enforcement. "
            "If the code satisfies (a)(b)(c) and does not regress them, "
            "output SEVERITY: NONE."
        ),
        "messages": [],
        "next_agent": "",
        "code": "",
        "security_report": "",
        "needs_fix": False,
        "fix_attempts": 0,
        "documentation": "",
        # GitHub 配置
        "github_owner": "homerLi007",
        "github_repo": "ai-coding-demo",
        "github_base_branch": "main",
        "github_code_path": "",
        "github_pr_url": ""
    }

    for event in app.stream(initial_inputs, stream_mode="values"):
        pass

    print("\n✨ Supervisor Workflow Finished Successfully!")


__all__ = ["workflow", "SupervisorState"]