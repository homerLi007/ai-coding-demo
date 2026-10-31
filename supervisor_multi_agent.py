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

# ==========================================
# 1. Define Advanced State
# ==========================================
class SupervisorState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    task: str
    next_agent: str          # Managed by supervisor: "coder", "security", "writer", or "FINISH"
    code: str
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
    """按 ### 切块，提取 [FIXABLE] 的最高优先级条目，保留完整修复方案。"""
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

    # 按 "###" 切块（要求 ### 在行首）
    blocks = re.split(r"(?m)^###\s+", report)
    parsed = []
    for b in blocks[1:]:                      # 第一块是摘要，跳过
        if "[INHERENT]" in b or "[INFO]" in b:
            continue                          # 不可修/信息性条目直接丢
        if "[FIXABLE]" not in b:
            continue                          # 没有标签的也不要
        m = re.search(r"\*\*Severity:\*\*\s*(CRITICAL|HIGH|MEDIUM|LOW)", b, re.I)
        sev = m.group(1).upper() if m else "MEDIUM"
        body = b.strip()
        # 单条最多 1200 字符，保留 "Fix:" 段落
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
    print("\n💻 [Coder Agent]: Writing code for the task...")
    task = state["task"]
    feedback = state.get("security_report", "")
    is_fixing = state.get("needs_fix", False)
    previous_code = state.get("code", "")
    previous_filename = state.get("github_code_path", "")
    fix_attempts = state.get("fix_attempts", 0)

    try:
        if is_fixing and feedback and previous_code:
            print(f"🔁 收到 security 反馈 (第 {fix_attempts + 1} 次修复)...")

            issues = _extract_top_issues(feedback, max_issues=3)
            if issues:
                short_feedback = "\n\n".join(issues)
            else:
                short_feedback = feedback[:3000]      # 兜底：从 1500 提到 3000

            allow_restructure = fix_attempts >= 1     # 第 2 次修复起允许重构

            prompt = (
                    "You are a senior Python security engineer doing an INCREMENTAL FIX "
                    "of an existing module.\n\n"
                    f"## Task\n{task}\n\n"
                    f"## Current code (file: {previous_filename})\n"
                    f"```python\n{previous_code}\n```\n\n"
                    f"## FIXABLE security issues (only these; [INHERENT] already filtered out)\n"
                    f"{short_feedback}\n\n"
                    "## Rules\n"
                    + (
                        "1. The auditor has already rejected MINIMAL DIFF patches. "
                        "You ARE allowed to restructure the deletion logic (e.g. switch to "
                        "`os.open(parent, O_DIRECTORY|O_NOFOLLOW)` + "
                        "`os.unlink(name, dir_fd=...)`). Do whatever it takes to satisfy "
                        "the listed [FIXABLE] issues.\n"
                        if allow_restructure else
                        "1. MINIMAL DIFF: keep existing structure; edit only the lines "
                        "causing the listed issues.\n"
                    )
                    + "2. DO NOT add `# KNOWN LIMITATION` comments for issues marked "
                      "[FIXABLE] — those MUST be fixed in code.\n"
                      "3. `os.unlink(name, dir_fd=...)` and `os.open(..., O_NOFOLLOW|O_DIRECTORY)` "
                      "ARE available in the Python stdlib on POSIX (3.3+). The excuse "
                      "\"atomic unlink-by-fd is not available\" is INVALID on POSIX. "
                      "Only on Windows may you fall back.\n"
                      "4. If an issue is genuinely [INHERENT] (not in the list above), "
                      "add a comment and mention it in 'notes'.\n\n"
                      "## Return format — ONLY valid JSON, no markdown fences\n"
                      '{"filename": "' + (previous_filename or "solution.py") + '", '
                                                                                '"code": "<full file contents>", '
                                                                                '"changes": "<1-2 sentences>", '
                                                                                '"notes": "<optional>"}\n'
            )

            response = llm_fix.invoke([HumanMessage(content=prompt)])
        else:
            prompt = (
                f"Write a clean, well-named Python function for this task: {task}\n\n"
                "Scope: keep it focused. Do NOT try to solve problems that require "
                "kernel primitives unavailable in pure Python; document known "
                "limitations with a comment instead.\n\n"
                "Choose a short snake_case filename ending with .py.\n"
                "Return ONLY valid JSON:\n"
                '{"filename": "foo.py", "code": "def foo():\\n    ...", '
                '"changes": "initial", "notes": ""}\n'
                "Do NOT wrap the JSON in markdown fences."
            )
            response = llm.invoke([HumanMessage(content=prompt)])

        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            payload = json.loads(content)
            raw_filename = payload.get("filename", "") or previous_filename or "solution.py"
            code = payload.get("code", "").strip()
            changes = payload.get("changes", "")
            notes = payload.get("notes", "")
        except json.JSONDecodeError:
            print("⚠️ Coder 未返回合法 JSON，使用兜底解析")
            code = content
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            raw_filename = previous_filename or "solution.py"
            changes = "(fallback)"
            notes = ""

        if not code:
            raise ValueError("LLM 返回的 code 为空")

        filename = sanitize_py_filename(raw_filename)
        print(f"📄 生成文件: {filename}")
        print(f"📝 改动说明: {changes}")
        if notes:
            print(f"⚠️ 已知局限: {notes}")
        print(f"📄 Code ({len(code)} 字符):\n{code}")

        # 如果修复后代码和旧代码一模一样，说明 LLM 没改，强制放行避免死循环
        if is_fixing and code.strip() == previous_code.strip():
            print("⚠️ 修复后代码与旧代码完全相同，判定为不可修，强制放行")
            return {
                "code": code,
                "github_code_path": filename,
                "security_report": "",
                "needs_fix": False,          # ← 强制放行
                "fix_attempts": fix_attempts + 1,
                "messages": [AIMessage(content=f"Coder: no change possible, passing through")],
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
    """Worker 2: 审计代码，返回结构化结果（含 needs_fix 标志）。"""
    print("\n🔒 [Security Auditor]: Auditing code for vulnerabilities...")
    code = state["code"]

    prompt = f"""Audit this Python code for security issues:
    
    <code>
    {code}
    </code>
    
    Output format (follow EXACTLY):
    Line 1: SEVERITY: <CRITICAL|HIGH|MEDIUM|LOW|NONE>
    Line 2: (blank line)
    Line 3+: Full markdown audit report.
    
    Severity rules:
    - CRITICAL : remote exploitable, data loss, RCE — must fix
    - HIGH     : serious vulnerability, exploitable under realistic conditions — must fix
    - MEDIUM   : real weakness requiring specific conditions — must fix
    - LOW      : minor weakness or best-practice deviation — must fix
    - NONE     : no actionable issue remains
    
    Only report issues that can be fixed by changing the code.
    Do NOT report purely informational observations (e.g. "hard links not handled", "docstring could be clearer") as findings — those are out of scope.
    If every remaining issue is purely informational and no code change would resolve it, output SEVERITY: NONE.
    
    Do NOT wrap the output in JSON or code fences.
    Use the SAME severity threshold consistently. Do NOT escalate an issue
    from LOW to HIGH across audits of functionally equivalent code.
    For every finding, prefix the body with one of:
    [FIXABLE]  - can be fixed by changing this code
    [INHERENT] - fundamental POSIX/kernel limitation, cannot be fixed in user space
    [INFO]     - informational, no code change needed
    """

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content.strip()

    m = re.search(r"SEVERITY\s*:\s*(CRITICAL|HIGH|MEDIUM|LOW|NONE)", content, re.IGNORECASE)
    if m:
        severity = m.group(1).upper()
    else:
        print("⚠️ 无法从首行解析 severity，默认按 MEDIUM 处理（不回退）")
        severity = "MEDIUM"

    needs_fix = severity != "NONE"

    # 熔断：修复次数超过上限后强制放行，避免 LOW 级别无限循环
    fix_attempts = state.get("fix_attempts", 0)

    LIMITS = {"CRITICAL": 30, "HIGH": 30, "MEDIUM": 6, "LOW": 3}

    if needs_fix and fix_attempts >= LIMITS.get(severity, 3):
        print(f"🚨 {severity} 已修 {fix_attempts} 次仍未通过，强制放行（PR 将带问题提交）")
        needs_fix = False

    report = content

    print(f"🛡️ Severity: {severity}, needs_fix: {needs_fix}, fix_attempts: {fix_attempts}")
    print(f"🛡️ Security Report:\n{report}")

    return {
        "security_report": report,
        "needs_fix": needs_fix,
        "messages": [AIMessage(content=f"Security Audit (severity={severity}):\n{report}")],
    }

def writer_node(state: SupervisorState):
    """Worker 3: Writes documentation for the code."""
    print("\n📝 [Doc Writer]: Generating documentation...")
    code = state["code"]

    prompt = f"Write a professional Markdown README documentation for this Python code:\n```python\n{code}\n```"
    response = llm.invoke([HumanMessage(content=prompt)])
    doc = response.content
    print(f"📚 Documentation generated successfully.")

    return {
        "documentation": doc,
        "messages": [AIMessage(content=f"Documentation:\n{doc}")]
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
    print("\n🐙 [GitHub Agent]: 提交代码与文档到仓库...")

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN 未设置，请检查 .env")

    g = Github(auth=Auth.Token(token))
    repo = g.get_repo(f"{state['github_owner']}/{state['github_repo']}")
    base_branch = repo.get_branch(state["github_base_branch"])

    new_branch_name = f"ai-agent/{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    try:
        repo.create_git_ref(
            ref=f"refs/heads/{new_branch_name}",
            sha=base_branch.commit.sha
        )
    except GithubException as e:
        if e.status != 422:
            raise

    # 🔑 动态文件名
    code_path = state.get("github_code_path") or "solution.py"

    files = {
        code_path: state.get("code", "") or "# empty",
        "README.md": state.get("documentation", "") or "# Generated by AI Agent",
    }

    for path, content in files.items():
        try:
            existing = repo.get_contents(path, ref=new_branch_name)
            repo.update_file(
                path=path, message=f"AI Agent: update {path}",
                content=content, sha=existing.sha, branch=new_branch_name
            )
        except GithubException:
            repo.create_file(
                path=path, message=f"AI Agent: add {path}",
                content=content, branch=new_branch_name
            )
        print(f"📄 已提交 {path}")

    try:
        pr = repo.create_pull(
            title=f"AI Agent: {state['task'][:60]}",
            body=f"**任务**: {state['task']}\n\n**安全审计**:\n{state.get('security_report', 'N/A')}",
            head=new_branch_name,
            base=state["github_base_branch"]
        )
        pr_url = pr.html_url
    except GithubException as e:
        if e.status == 422:
            pr_url = next(
                (p.html_url for p in repo.get_pulls(
                    state="open",
                    head=f"{repo.owner.login}:{new_branch_name}"
                )), ""
            )
        else:
            raise

    print(f"✅ PR: {pr_url}")
    return {
        "github_pr_url": pr_url,
        "messages": [AIMessage(content=f"GitHub PR: {pr_url}")]
    }

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