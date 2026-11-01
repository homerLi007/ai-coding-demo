import os
import json
import re
from typing import Annotated, TypedDict, Sequence, Literal
import operator
from datetime import datetime

from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from dotenv import load_dotenv
from github import Github, Auth, GithubException

load_dotenv()


# ==========================================
# 0. 多文件项目的辅助函数与 Prompt 常量
# ==========================================

_ALLOWED_EXTS = {
    ".java", ".kt", ".gradle", ".xml", ".properties", ".yml", ".yaml",
    ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".css",
    ".py", ".md", ".txt",
}

# 无扩展名但合法、必须保留原名的文件
_NO_EXT_WHITELIST = {
    "dockerfile", "makefile", ".gitignore", ".dockerignore",
    ".npmrc", ".env.example", ".editorconfig",
}


def sanitize_filename(name: str) -> str:
    """把 LLM 返回的相对路径清洗成安全的、仓库内可用的路径。

    - 拒绝绝对路径、`..`、NUL
    - 保留子目录（Spring Boot 的 src/main/java/... 需要保留）
    - 保留合法扩展名；无扩展名的补 `.txt`（白名单除外）
    """
    if not name or not isinstance(name, str):
        return "README.md"

    name = name.replace("\\", "/").strip().lstrip("/")

    parts = []
    for p in name.split("/"):
        p = p.strip()
        if p in ("", ".", "..") or "\x00" in p:
            continue
        p = re.sub(r"[^A-Za-z0-9_\-.]", "_", p)
        if p:
            parts.append(p)

    if not parts:
        return "README.md"

    base = parts[-1]
    if base.lower() not in _NO_EXT_WHITELIST:
        _, ext = os.path.splitext(base)
        if ext.lower() not in _ALLOWED_EXTS:
            parts[-1] = base + ".txt"

    return "/".join(parts)


def _format_files_for_prompt(files: dict, max_chars: int = 12000) -> str:
    """把多文件拼成给 LLM 的展示格式，超长则逐文件截断。"""
    if not files:
        return "(no files yet)"
    out = []
    used = 0
    for path in sorted(files.keys()):
        content = files[path] or ""
        block = f"### {path}\n```\n{content}\n```\n"
        if used + len(block) > max_chars:
            out.append(
                f"### {path}\n(truncated — assume unchanged unless "
                f"a listed issue targets it)\n"
            )
            continue
        out.append(block)
        used += len(block)
    return "\n".join(out)


INITIAL_PROMPT = """You are a senior full-stack engineer. Generate a small but complete
**Spring Boot backend + React frontend** project for the task below.

## Task
{task}

## Output requirements
Return a JSON object with THREE fields:
- "files": {{ "<relative path>": "<file content>", ... }}
- "changes": "1 sentence describing what you built"
- "notes": "optional, known limitations"

## File layout (use EXACTLY these paths)
Generate these files at minimum:

backend/pom.xml
backend/src/main/java/com/example/demo/DemoApplication.java
backend/src/main/java/com/example/demo/controller/ApiController.java
backend/src/main/resources/application.properties
frontend/package.json
frontend/vite.config.js
frontend/index.html
frontend/src/main.jsx
frontend/src/App.jsx
frontend/src/App.css

## Rules
1. Spring Boot 3.x, Java 17, Maven. Use `spring-boot-starter-parent` to manage
   dependency versions — do NOT hardcode dependency versions.
2. Exactly ONE `@RestController` with at least one endpoint that satisfies the
   task. No database, no auth, no Spring Security.
3. React 18 + Vite + plain JavaScript (no TypeScript). App.jsx calls the backend
   via `fetch('/api/...')` — relative path, same-origin in production.
4. Keep each file SHORT (under 120 lines). Do not add extra features, extra
   controllers, or extra React components.
5. Do not invent endpoints the React code does not call; do not call endpoints
   the backend does not expose.
6. Return ONLY valid JSON. No markdown fences, no prose before/after.
"""


FIX_PROMPT = """You are a senior full-stack engineer fixing security issues in an
existing **Spring Boot + React** project.

## Task
{task}

## Current project files
{files_block}

## Security issues that MUST be fixed (only these)
{issues_block}

## Rules
1. MINIMAL DIFF: keep existing structure, package names, file paths. Edit only
   what the listed issues require.
2. DO NOT rewrite the whole project. If a file is not related to a listed issue,
   do not touch it.
3. If an issue is about a specific file, keep the fix local to that file.
4. Do not add dependencies to pom.xml or package.json unless a listed issue
   explicitly requires it.
5. Do not add logging, comments, or documentation that is not required by the
   issue.
6. If an issue is genuinely [INHERENT], add a code comment
   `// KNOWN LIMITATION: ...` (Java) or `// KNOWN LIMITATION: ...` (JS) and
   note it in `notes`.
7. Return ONLY valid JSON, no markdown fences.

## Return format
{{
  "files": {{ "<path>": "<updated content>", ... }},
  "changes": "1-2 sentences",
  "notes": "optional"
}}

Only include files that actually changed. Files not listed in `files` will be
kept as-is.
"""


# ==========================================
# 1. Define Advanced State
# ==========================================
class SupervisorState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    task: str
    next_agent: str
    code: str                       # 主文件内容（兼容旧逻辑 / supervisor 判断"是否已生成"）
    files: dict[str, str]           # {相对路径: 文件内容}
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
            continue
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
    temperature=0.3,
)

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
            print("⚠️ Coder 未返回合法 JSON，兜底为单文件 README.md")
            raw_files = {"README.md": content}
            changes = "(fallback)"
            notes = ""

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
            merged.update(cleaned)
        else:
            merged = cleaned

        # 主文件：优先 DemoApplication.java，其次 pom.xml，最后随便一个
        primary = next(
            (p for p in merged if p.endswith("DemoApplication.java")),
            next(
                (p for p in merged if p.endswith("pom.xml")),
                next(iter(merged)),
            ),
        )
        primary_content = merged[primary]

        print(f"📄 生成 {len(merged)} 个文件:")
        for p in sorted(merged.keys()):
            print(f"   - {p} ({len(merged[p])} 字符)")
        print(f"📝 改动说明: {changes}")
        if notes:
            print(f"⚠️ 已知局限: {notes}")

        if is_fixing and cleaned == {k: previous_files.get(k) for k in cleaned}:
            print("⚠️ 修复后文件无变化，强制放行")

        return {
            "files": merged,
            "code": primary_content,
            "github_code_path": primary,
            "security_report": "",
            "needs_fix": False,
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [
                AIMessage(content=f"Coder: {len(merged)} files, {changes}")
            ],
        }

    except Exception as exc:
        print(f"❌ Coder 异常: {type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
        return {
            "files": previous_files,
            "code": state.get("code", ""),
            "github_code_path": state.get("github_code_path", ""),
            "security_report": "",
            "needs_fix": False,
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

    print(
        f"🛡️ Severity: {severity}, needs_fix: {needs_fix}, "
        f"fix_attempts: {fix_attempts}"
    )
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
- `## How to run` — backend: `cd backend && mvn spring-boot:run`;
  frontend: `cd frontend && npm install && npm run dev`
- `## API` — list endpoints from the controller
- `## Notes` — any known limitations

Return ONLY the raw README.md content. Do NOT wrap in a code fence.
"""
    response = llm.invoke([HumanMessage(content=prompt)])
    doc = response.content.strip()

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

    files = dict(state.get("files", {}) or {})
    files["README.md"] = state.get("documentation", "") or "# Generated by AI Agent"

    for path, content in files.items():
        try:
            existing = repo.get_contents(path, ref=new_branch)
            repo.update_file(
                path=path,
                message=f"AI Agent: update {path}",
                content=content or "",
                sha=existing.sha,
                branch=new_branch,
            )
        except GithubException:
            repo.create_file(
                path=path,
                message=f"AI Agent: add {path}",
                content=content or "",
                branch=new_branch,
            )
        print(f"   📄 已提交 {path}")

    try:
        pr = repo.create_pull(
            title=f"AI Agent: {state['task'][:60]}",
            body=(
                f"**任务**: {state['task']}\n\n"
                f"**安全审计**:\n{state.get('security_report', 'N/A')}"
            ),
            head=new_branch,
            base=state["github_base_branch"],
        )
        pr_url = pr.html_url
    except GithubException as e:
        if e.status == 422:
            pr_url = next(
                (
                    p.html_url
                    for p in repo.get_pulls(
                    state="open",
                    head=f"{repo.owner.login}:{new_branch}",
                )
                ),
                "",
            )
        else:
            raise

    print(f"✅ PR: {pr_url}")
    return {
        "github_pr_url": pr_url,
        "messages": [AIMessage(content=f"PR: {pr_url}")],
    }


# ==========================================
# 5. Define Conditional Routing Edge
# ==========================================
def route_next(
        state: SupervisorState,
) -> Literal["coder", "security", "writer", "github", "END"]:
    next_agent = state["next_agent"]
    if next_agent in ("coder", "security", "writer", "github"):
        return next_agent
    return "END"


# ==========================================
# 6. Assemble the Supervisor Graph
# ==========================================
workflow = StateGraph(SupervisorState)

workflow.add_node("supervisor", supervisor_node)
workflow.add_node("coder", coder_node)
workflow.add_node("security", security_node)
workflow.add_node("writer", writer_node)
workflow.add_node("github", github_commit_node)

workflow.set_entry_point("supervisor")

workflow.add_edge("coder", "supervisor")
workflow.add_edge("security", "supervisor")
workflow.add_edge("writer", "supervisor")
workflow.add_edge("github", "supervisor")

workflow.add_conditional_edges(
    "supervisor",
    route_next,
    {
        "coder": "coder",
        "security": "security",
        "writer": "writer",
        "github": "github",
        "END": END,
    },
)

app = workflow.compile()


# ==========================================
# 7. Run the Supervisor Multi-Agent System
# ==========================================
if __name__ == "__main__":
    print("🚀 Supervisor Multi-Agent System Started...")

    initial_inputs = {
        "task": (
            "Create a Spring Boot 3 + React 18 project: a REST endpoint that "
            "takes a list of file paths and safely deletes them "
            "(refuse symlinks, refuse paths outside allowed_root, refuse "
            "non-regular files), plus a React UI to call it. "
            "EXPLICIT NON-GOALS (do not report as findings): TOCTOU, "
            "Windows ADS, 8.3 names, hard links, secure erasure, permission "
            "enforcement."
        ),
        "messages": [],
        "next_agent": "",
        "code": "",
        "files": {},                # ← 补上：多文件字典
        "security_report": "",
        "needs_fix": False,
        "fix_attempts": 0,
        "documentation": "",
        "iteration_count": 0,       # ← 补上：supervisor 熔断依赖
        "github_owner": "homerLi007",
        "github_repo": "ai-coding-demo",
        "github_base_branch": "main",
        "github_code_path": "",
        "github_pr_url": "",
    }

    for event in app.stream(initial_inputs, stream_mode="values"):
        pass

    print("\n✨ Supervisor Workflow Finished Successfully!")


__all__ = ["app", "workflow", "SupervisorState"]