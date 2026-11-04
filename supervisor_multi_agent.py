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
import subprocess
import tempfile
import time
import socket

load_dotenv()


# ==========================================
# 0. 多文件项目的辅助函数与 Prompt 常量
# ==========================================

_ALLOWED_EXTS = {
    ".java", ".kt", ".gradle", ".xml", ".properties", ".yml", ".yaml",
    ".js", ".jsx", ".ts", ".tsx", ".json", ".html", ".css",
    ".py", ".md", ".txt",
}

def _port_ready(host: str, port: int, timeout: float = 60.0) -> bool:
    """等待端口就绪"""
    start = time.time()
    while time.time() - start < timeout:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except OSError:
            time.sleep(0.5)
    return False


def _write_files_to_dir(files: dict, base_dir: str, prefix: str) -> None:
    """把 files 里以 prefix 开头的文件写到 base_dir 下"""
    for path, content in files.items():
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        full = os.path.join(base_dir, rel)
        os.makedirs(os.path.dirname(full) or base_dir, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content or "")


def _run_mvn_test(files: dict, timeout: int = 300) -> tuple[bool, str]:
    """在临时目录里跑 `mvn test`，返回 (passed, output)"""
    if not any(p.startswith("backend/") for p in files):
        return False, "no backend/ files to test"
    with tempfile.TemporaryDirectory() as tmp:
        backend = os.path.join(tmp, "backend")
        os.makedirs(backend)
        _write_files_to_dir(files, backend, "backend/")
        try:
            r = subprocess.run(
                ["mvn", "test", "-B", "-q"],
                cwd=backend, capture_output=True, text=True, timeout=timeout,
            )
            return r.returncode == 0, (r.stdout or "") + "\n" + (r.stderr or "")
        except subprocess.TimeoutExpired:
            return False, f"mvn test timed out after {timeout}s"
        except FileNotFoundError:
            return False, "mvn not found in PATH (check Dockerfile)"
        except Exception as e:
            return False, f"mvn test failed: {e}"


def _run_e2e(files: dict, e2e_files: dict, timeout: int = 300) -> tuple[bool, str]:
    """启动 Spring Boot + React，跑 Playwright，返回 (passed, output)"""
    backend_proc = None
    frontend_proc = None
    with tempfile.TemporaryDirectory() as tmp:
        backend = os.path.join(tmp, "backend")
        frontend = os.path.join(tmp, "frontend")
        e2e_dir = os.path.join(tmp, "e2e")
        for d in (backend, frontend, e2e_dir):
            os.makedirs(d)
        _write_files_to_dir(files, backend, "backend/")
        _write_files_to_dir(files, frontend, "frontend/")
        _write_files_to_dir(e2e_files, e2e_dir, "e2e/")

        try:
            # 1. 编译后端
            r = subprocess.run(
                ["mvn", "package", "-DskipTests", "-B", "-q"],
                cwd=backend, capture_output=True, text=True, timeout=180,
            )
            if r.returncode != 0:
                return False, "mvn package failed:\n" + r.stdout + r.stderr

            # 2. 启动后端
            target = os.path.join(backend, "target")
            jars = [f for f in os.listdir(target)
                    if f.endswith(".jar") and "sources" not in f and "original" not in f]
            if not jars:
                return False, "no jar found in target/"
            backend_proc = subprocess.Popen(
                ["java", "-jar", os.path.join(target, jars[0])],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if not _port_ready("localhost", 8080, timeout=90):
                return False, "backend did not start within 90s"

            # 3. 安装前端依赖
            r = subprocess.run(
                ["npm", "install", "--registry=https://registry.npmmirror.com"],
                cwd=frontend, capture_output=True, text=True, timeout=180,
            )
            if r.returncode != 0:
                return False, "npm install failed:\n" + r.stdout + r.stderr

            # 4. 启动前端
            frontend_proc = subprocess.Popen(
                ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"],
                cwd=frontend,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            if not _port_ready("localhost", 5173, timeout=90):
                return False, "frontend did not start within 90s"

            # 5. 安装 e2e 依赖 + 跑 Playwright
            r = subprocess.run(
                ["npm", "install", "--registry=https://registry.npmmirror.com"],
                cwd=e2e_dir, capture_output=True, text=True, timeout=180,
            )
            if r.returncode != 0:
                return False, "e2e npm install failed:\n" + r.stdout + r.stderr

            r = subprocess.run(
                ["npx", "playwright", "test", "--reporter=line"],
                cwd=e2e_dir, capture_output=True, text=True, timeout=timeout,
                env={**os.environ, "BASE_URL": "http://localhost:5173"},
            )
            return r.returncode == 0, (r.stdout or "") + "\n" + (r.stderr or "")
        except subprocess.TimeoutExpired as e:
            return False, f"e2e timed out: {e}"
        except Exception as e:
            return False, f"e2e failed: {e}"
        finally:
            for proc in (backend_proc, frontend_proc):
                if proc and proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()

# 无扩展名但合法、必须保留原名的文件
_NO_EXT_WHITELIST = {
    "dockerfile", "makefile", ".gitignore", ".dockerignore",
    ".npmrc", ".env.example", ".editorconfig",
}

def _check_hard_constraints(files: dict) -> str | None:
    """静态检查生成的项目是否违反 HARD CONSTRAINTS。
    返回 None 表示通过，否则返回违规描述。
    """
    pom = files.get("backend/pom.xml", "")
    if "spring-boot-starter-security" in pom:
        return (
            "HARD CONSTRAINT VIOLATED: pom.xml contains "
            "spring-boot-starter-security. Remove it and delete any security "
            "config classes."
        )

    # 检查所有 Java 文件
    java_files = {p: c for p, c in files.items()
                  if p.startswith("backend/src/main/java/") and p.endswith(".java")}
    for path, content in java_files.items():
        for forbidden in ("UserDetailsService", "SecurityFilterChain",
                          "WebSecurityConfigurerAdapter", "ADMIN_PASSWORD",
                          "BCryptPasswordEncoder"):
            if forbidden in content:
                return (
                    f"HARD CONSTRAINT VIOLATED: {path} references "
                    f"'{forbidden}'. Delete this file and any related "
                    f"security code."
                )
        # 检查 @Value 是否带默认值
        for m in re.finditer(r'@Value\("([^"]+)"\)', content):
            expr = m.group(1)
            if expr.startswith("${") and ":" not in expr:
                return (
                    f"HARD CONSTRAINT VIOLATED: {path} uses "
                    f'@Value("{expr}") without a default. Use '
                    f'@Value("${{{expr[2:-1]}:someDefault}}") or hardcode it.'
                )

    # 检查 application.properties 里的 ${...} 占位符
    props = files.get("backend/src/main/resources/application.properties", "")
    for line in props.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if "${" in value:
            return (
                f"HARD CONSTRAINT VIOLATED: application.properties has "
                f"'{key}={value}' — no ${{ENV}} placeholders allowed. Use a "
                f"literal default value."
            )

    return None

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

## HARD CONSTRAINTS (violating any of these = invalid output)
1. **ZERO environment variables.** The generated app MUST start with plain
   `mvn spring-boot:run` and NO environment variables set. Every value must
   have a reasonable default, either hardcoded or in `application.properties`.
2. **DO NOT add `spring-boot-starter-security` to pom.xml.** The only allowed
   Spring Boot starters are: `spring-boot-starter-web` and
   `spring-boot-starter-test` (test scope).
3. **DO NOT create any `SecurityConfig`, `WebSecurityConfig`, `UserDetailsService`,
   `PasswordEncoder`, or anything under a `config/` or `security/` package.**
4. **DO NOT use `@Value("${{SOMETHING}}")` without a default.** If you must read a
   property, write `@Value("${{SOMETHING:literal-default}}")`.
5. **DO NOT put `${{ENV_VAR}}` placeholders in `application.properties`.**
   Any key there must have a literal value.
6. The only Java files allowed under `src/main/java/com/example/demo/` are:
   `DemoApplication.java` and one file in `controller/`. Nothing else.
7. No database, no auth, no JWT, no Spring Security, no Actuator, no Swagger.

## Rules
1. Spring Boot 3.x, Java 17, Maven. Use `spring-boot-starter-parent` to manage
   dependency versions — do NOT hardcode dependency versions.
2. Exactly ONE `@RestController` with at least one endpoint that satisfies the
   task.
3. React 18 + Vite + plain JavaScript (no TypeScript). App.jsx calls the backend
   via `fetch('/api/...')` — relative path, same-origin in production.
4. Keep each file SHORT (under 120 lines). Do not add extra features.
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
    # ── 测试相关（新增）──
    test_report: str           # mvn test 的完整输出
    test_passed: bool          # mvn test 是否通过
    test_attempts: int         # 测试修复次数（用于熔断）
    e2e_report: str            # Playwright 输出
    e2e_passed: bool
    e2e_attempts: int
    generated_repo: str        # 新仓库全名，如 "homerLi007/generated-app-20261101-143052"
    generated_repo_url: str    # https://github.com/homerLi007/generated-app-...
    app_url: str               # http://172.30.95.100:31234/
    app_nodeport: int          # 分配的 NodePort


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
            "security_report": "",       # 重新审计
            "test_report": "",           # ← 新增：重新测试
            "test_passed": False,        # ← 新增
            "e2e_report": "",            # ← 新增：重新 E2E
            "e2e_passed": False,         # ← 新增
            "needs_fix": False,
            "fix_attempts": fix_attempts + (1 if is_fixing else 0),
            "messages": [AIMessage(content=f"Coder: {len(merged)} files, {changes}")],
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

    # ── 先做静态硬约束检查（不调用 LLM，快且确定）──
    violation = _check_hard_constraints(files)
    if violation:
        print(f"🚫 {violation}")
        severity = "CRITICAL"
        needs_fix = True
        fix_attempts = state.get("fix_attempts", 0)
        BLOCKING_LIMIT = 10
        if fix_attempts >= BLOCKING_LIMIT:
            print(f"🚨 已修 {fix_attempts} 次仍未通过，强制放行")
            needs_fix = False
        return {
            "security_report": (
                f"SEVERITY: {severity}\n\n"
                f"## Hard-constraint violation\n\n"
                f"{violation}\n\n"
                f"**Severity:** CRITICAL\n"
                f"**Finding:** [FIXABLE] {violation}\n"
            ),
            "needs_fix": needs_fix,
            "messages": [AIMessage(content=f"Security (CRITICAL): {violation}")],
        }

    # ── 静态检查通过，继续 LLM 审计 ──
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

    # 1. 首次生成
    if not state.get("code"):
        next_agent = "coder"
    # 2. security 打回
    elif state.get("needs_fix"):
        next_agent = "coder"
    # 3. 无安全审计
    elif not state.get("security_report"):
        next_agent = "security"
    # 4. 无测试报告（且未熔断）
    elif not state.get("test_report") and state.get("test_attempts", 0) < 3:
        next_agent = "tester"
    # 5. 测试失败打回（最多 3 次）
    elif state.get("test_attempts", 0) < 3 and state.get("test_report") and not state.get("test_passed"):
        next_agent = "coder"
    # 6. 无 E2E 报告（且未熔断）
    elif not state.get("e2e_report") and state.get("e2e_attempts", 0) < 3:
        next_agent = "e2e_tester"
    # 7. E2E 失败打回（最多 3 次）
    elif state.get("e2e_attempts", 0) < 3 and state.get("e2e_report") and not state.get("e2e_passed"):
        next_agent = "coder"
    # 8. 无文档
    elif not state.get("documentation"):
        next_agent = "writer"
    # 9. 无 PR
    elif not state.get("github_pr_url"):
        next_agent = "github"
    else:
        next_agent = "FINISH"

    iteration = state.get("iteration_count", 0) + 1
    if iteration > 80:              # 加测试后流程更长，上限从 50 提到 80
        next_agent = "FINISH"

    print(f"🎯 Supervisor Decision -> Next Worker: {next_agent}")
    return {"next_agent": next_agent, "iteration_count": iteration}


def _gen_app_dockerfile() -> str:
    return """\
FROM node:20-alpine AS fe
WORKDIR /fe
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM maven:3.9-eclipse-temurin-17 AS be
WORKDIR /app
COPY backend/pom.xml .
RUN mvn dependency:go-offline -B || true
COPY backend/src ./src
COPY --from=fe /fe/dist ./src/main/resources/static/
RUN mvn package -DskipTests -B

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=be /app/target/*.jar app.jar
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]
"""


def _gen_app_deployment(repo_name: str) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {repo_name}
  namespace: default
  labels:
    app: {repo_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {repo_name}
  template:
    metadata:
      labels:
        app: {repo_name}
    spec:
      containers:
        - name: app
          image: ghcr.io/homerli007/{repo_name}:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "200m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      imagePullSecrets:
        - name: ghcr-secret
"""


def _gen_app_service(repo_name: str, nodeport: int) -> str:
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {repo_name}-service
  namespace: default
spec:
  selector:
    app: {repo_name}
  ports:
    - port: 80
      targetPort: 8080
      nodePort: {nodeport}
  type: NodePort
"""


def _gen_app_workflow(repo_name: str) -> str:
    return f"""\
name: Build and Deploy

on:
  push:
    branches: [ main ]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: homerli007/{repo_name.lower()}
  KUBECONFIG: /home/forrestli/.kube/config

jobs:
  deploy:
    runs-on: [self-hosted, k3s, local]

    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        run: |
          cd "$GITHUB_WORKSPACE"
          find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {{}} + 2>/dev/null || true
          git clone --depth 1 "git@github.com:${{GITHUB_REPOSITORY}}.git" .
          git fetch --depth 1 origin "${{GITHUB_SHA}}"
          git checkout "${{GITHUB_SHA}}"

      - name: Log in to GHCR
        run: |
          echo "${{{{ secrets.GITHUB_TOKEN }}}}" | \\
            docker login "$REGISTRY" -u "${{{{ github.actor }}}}" --password-stdin

      - name: Build and push image
        run: |
          IMG="${{REGISTRY}}/${{IMAGE_NAME}}:${{GITHUB_SHA}}"
          IMG_LATEST="${{REGISTRY}}/${{IMAGE_NAME}}:latest"
          docker build -f backend/Dockerfile -t "$IMG" -t "$IMG_LATEST" .
          docker push "$IMG"
          docker push "$IMG_LATEST"

      - name: Deploy to K3s
        run: |
          IMG="${{REGISTRY}}/${{IMAGE_NAME}}:${{GITHUB_SHA}}"
          sed -i "s|image:.*|image: ${{IMG}}|" k8s/deployment.yaml
          kubectl apply -f k8s/
          kubectl rollout status deployment/{repo_name} -n default --timeout=300s
          kubectl get svc {repo_name}-service -n default
"""


def _push_all_files(repo, files: dict):
    """用 Git Data API 一次性提交所有文件到 main（避免逐个 create_file 的 40+ API 调用）。"""
    # 1. 获取 main 分支（空 repo，还没有 commit）
    try:
        ref = repo.get_git_ref("heads/main")
        parent_commit = repo.get_git_commit(ref.object.sha)
        base_tree_sha = parent_commit.tree.sha
    except GithubException:
        # 空 repo，没有 main
        parent_commit = None
        base_tree_sha = None

    # 2. 创建 blob
    blobs = {}
    for path, content in files.items():
        blob = repo.create_git_blob(content or "", "utf-8")
        blobs[path] = blob.sha

    # 3. 创建 tree（带子目录）
    tree_items = []
    for path, blob_sha in blobs.items():
        parts = path.split("/")
        mode = "100644"
        # 简化：用完整 path 作为 tree entry（GitHub 支持 path 带 / 吗？不支持，必须建子树）
        # 改用逐个 push 更稳
    # → 实际上 GitHub 的 create_git_tree 不支持 path 带 /，需要递归建子树。
    # 为了简单可靠，退回逐个 create_file/update_file（文件数 10 个左右，可接受）
    for path, content in files.items():
        try:
            existing = repo.get_contents(path)
            repo.update_file(path, f"AI Agent: update {path}", content or "", existing.sha)
        except GithubException:
            repo.create_file(path, f"AI Agent: add {path}", content or "")

import time
import random


def _gen_app_dockerfile() -> str:
    return """\
FROM node:20-alpine AS fe
WORKDIR /fe
RUN npm config set registry https://registry.npmmirror.com
COPY frontend/package*.json ./
RUN npm ci || npm install
COPY frontend/ ./
RUN npm run build

FROM maven:3.9-eclipse-temurin-17 AS be
WORKDIR /app
COPY backend/pom.xml .
RUN mvn dependency:go-offline -B || true
COPY backend/src ./src
COPY --from=fe /fe/dist ./src/main/resources/static/
RUN mvn package -DskipTests -B

FROM eclipse-temurin:17-jre
WORKDIR /app
COPY --from=be /app/target/*.jar app.jar
EXPOSE 8080
CMD ["java", "-jar", "app.jar"]
"""


def _gen_app_deployment(repo_name: str, owner: str) -> str:
    return f"""\
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {repo_name}
  namespace: default
  labels:
    app: {repo_name}
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {repo_name}
  template:
    metadata:
      labels:
        app: {repo_name}
    spec:
      containers:
        - name: app
          image: ghcr.io/{owner}/{repo_name}:latest
          imagePullPolicy: Always
          ports:
            - containerPort: 8080
          resources:
            requests:
              cpu: "200m"
              memory: "512Mi"
            limits:
              cpu: "1000m"
              memory: "1Gi"
      imagePullSecrets:
        - name: ghcr-secret
"""


def _gen_app_service(repo_name: str, nodeport: int) -> str:
    return f"""\
apiVersion: v1
kind: Service
metadata:
  name: {repo_name}-service
  namespace: default
spec:
  selector:
    app: {repo_name}
  ports:
    - port: 80
      targetPort: 8080
      nodePort: {nodeport}
  type: NodePort
"""


def _gen_app_workflow(repo_name: str, owner: str) -> str:
    return f"""\
name: Build and Deploy

on:
  push:
    branches: [ main ]
  workflow_dispatch:

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: {owner}/{repo_name.lower()}
  KUBECONFIG: /home/forrestli/.kube/config

jobs:
  deploy:
    runs-on: [self-hosted, k3s, local]

    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        run: |
          cd "$GITHUB_WORKSPACE"
          find . -mindepth 1 -maxdepth 1 ! -name '.git' -exec rm -rf {{}} + 2>/dev/null || true
          git clone --depth 1 "git@github.com:${{GITHUB_REPOSITORY}}.git" .
          git fetch --depth 1 origin "${{GITHUB_SHA}}"
          git checkout "${{GITHUB_SHA}}"

      - name: Log in to GHCR
        run: |
          echo "${{{{ secrets.GITHUB_TOKEN }}}}" | \\
            docker login "$REGISTRY" -u "${{{{ github.actor }}}}" --password-stdin

      - name: Build and push image
        run: |
          IMG="${{REGISTRY}}/${{IMAGE_NAME}}:${{GITHUB_SHA}}"
          IMG_LATEST="${{REGISTRY}}/${{IMAGE_NAME}}:latest"
          docker build -f backend/Dockerfile -t "$IMG" -t "$IMG_LATEST" .
          docker push "$IMG"
          docker push "$IMG_LATEST"

      - name: Deploy to K3s
        run: |
          IMG="${{REGISTRY}}/${{IMAGE_NAME}}:${{GITHUB_SHA}}"
          sed -i "s|image:.*|image: ${{IMG}}|" k8s/deployment.yaml
          kubectl apply -f k8s/
          kubectl rollout status deployment/{repo_name} -n default --timeout=300s
          kubectl get svc {repo_name}-service -n default
"""


def github_commit_node(state: SupervisorState):
    print("\n🐙 [GitHub Agent]: 创建新仓库并推送生成的项目...")
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise ValueError("GITHUB_TOKEN 未设置")

    g = Github(auth=Auth.Token(token))
    owner = (state.get("github_owner") or "").strip()
    print(f"🎯 github_owner = {owner!r}")

    # ── 1. 拿当前认证用户 ──
    auth_user = g.get_user()   # ← 注意：不带参数 = AuthenticatedUser，有 create_repo
    print(f"👤 当前认证用户: {auth_user.login}")

    # ── 2. 决定目标 owner 对象 ──
    target_owner = None
    if not owner or owner.lower() == auth_user.login.lower():
        target_owner = auth_user
        owner = auth_user.login
        print(f"📦 将在 user 下创建: {owner}")
    else:
        try:
            target_owner = g.get_organization(owner)
            print(f"📦 将在 organization 下创建: {owner}")
        except GithubException as e:
            raise RuntimeError(
                f"无法访问 organization '{owner}' (status={e.status})。"
                f"请检查：① org 名是否正确（你的是 'homerli007-ai'）；"
                f"② PAT 是否有 org 的写权限（Classic PAT 需勾 'repo' + 'admin:org'，"
                f"Fine-grained PAT 需在 org 下创建并授予 'Administration: Read and write'）。"
            )

    # ── 3. 生成唯一 repo 名 + NodePort ──
    import random
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    repo_name = f"generated-app-{ts}"
    nodeport = 30080 + random.randint(0, 2600)
    print(f"📦 新仓库: {owner}/{repo_name}")
    print(f"🔌 NodePort: {nodeport}")

    # ── 4. 创建 repo ──
    try:
        new_repo = target_owner.create_repo(
            name=repo_name,
            description=f"AI Agent generated: {state['task'][:100]}",
            private=False,
            auto_init=False,
        )
        print(f"✅ Repo created: {new_repo.html_url}")
    except GithubException as e:
        raise RuntimeError(f"创建仓库失败: status={e.status}, data={e.data}")

    # ... 下面保持原样（组装 files、create_file、返回 State） ...

    # ── 4. 组装要推送的文件 ──
    files = dict(state.get("files", {}) or {})
    for path, content in (state.get("files", {}) or {}).items():
        files[f"/{path.lstrip('/')}"] = content
    files["/README.md"] = state.get("documentation", "") or "# Generated App"
    files["/backend/Dockerfile"] = _gen_app_dockerfile()
    files["k8s/deployment.yaml"] = _gen_app_deployment(repo_name, owner)
    files["k8s/service.yaml"] = _gen_app_service(repo_name, nodeport)
    files[".github/workflows/deploy.yml"] = _gen_app_workflow(repo_name, owner)

    # ── 5. 逐个 create_file（文件数 10 个左右，逐个推简单可靠）──
    try:
        for path, content in files.items():
            new_repo.create_file(
                path=path,
                message=f"AI Agent: add {path}",
                content=content or "",
            )
            print(f"   📄 已提交 {path}")
    except GithubException as e:
        raise RuntimeError(f"推送文件失败: {e.status} {e.data}")

    # ── 6. 计算 app URL ──
    node_ip = os.environ.get("K3S_NODE_IP", "172.30.95.100")
    app_url = f"http://{node_ip}:{nodeport}/"
    print(f"🌐 App URL (预计): {app_url}")
    print(f"⏳ 新 repo 的 CI 已触发，2-3 分钟后可访问")

    return {
        "generated_repo": f"{owner}/{repo_name}",
        "generated_repo_url": new_repo.html_url,
        "app_url": app_url,
        "app_nodeport": nodeport,
        "github_pr_url": new_repo.html_url,
        "messages": [AIMessage(content=f"New repo: {new_repo.html_url}, App: {app_url}")],
    }

TESTER_PROMPT = """Generate JUnit 5 tests for this Spring Boot project.

## Project files (backend only)
{backend_files}

## Output format
Return JSON:
{{
  "files": {{ "<path>": "<file content>", ... }},
  "notes": "optional"
}}

## Rules
1. Use `@SpringBootTest` + `MockMvc` for controller tests — do NOT start a real server.
2. Put tests under `backend/src/test/java/com/example/demo/`.
3. At least one test that hits the main endpoint and asserts the response status.
4. Keep each test file under 80 lines. Do not test things that require a database.
5. Return ONLY valid JSON, no markdown fences.
"""


def tester_node(state: SupervisorState):
    print("\n🧪 [Tester Agent]: Generating JUnit tests...")
    files = state.get("files", {}) or {}
    attempts = state.get("test_attempts", 0) + 1

    backend_files = {k: v for k, v in files.items() if k.startswith("backend/")}
    if not backend_files:
        return {
            "test_report": "No backend files to test",
            "test_passed": False,
            "test_attempts": attempts,
            "messages": [AIMessage(content="Tester: no backend files")],
        }

    try:
        prompt = TESTER_PROMPT.format(
            backend_files=_format_files_for_prompt(backend_files, max_chars=10000)
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            payload = json.loads(content)
            test_files = payload.get("files", {})
        except json.JSONDecodeError:
            return {
                "test_report": "LLM failed to return valid JSON for tests",
                "test_passed": False,
                "test_attempts": attempts,
                "messages": [AIMessage(content="Tester: JSON parse failed")],
            }

        # 清洗测试文件路径
        cleaned = {}
        for p, c in test_files.items():
            if isinstance(c, str):
                cleaned[sanitize_filename(p)] = c

        print(f"📄 生成 {len(cleaned)} 个测试文件")
        # 合并到 files 里一起跑（但不写回 state.files，避免提交到 PR）
        all_files = {**files, **cleaned}

        print("⏳ 运行 mvn test...")
        passed, output = _run_mvn_test(all_files, timeout=300)

        # 只保留输出末尾 3000 字符，避免 state 太大
        report = f"=== mvn test ({'PASSED' if passed else 'FAILED'}) ===\n{output[-3000:]}"
        print(f"🛡️ Test result: {'PASSED' if passed else 'FAILED'}")

        return {
            "test_report": report,
            "test_passed": passed,
            "test_attempts": attempts,
            "messages": [AIMessage(content=f"Tester: {'PASSED' if passed else 'FAILED'}")],
        }
    except Exception as e:
        print(f"❌ Tester 异常: {e}")
        return {
            "test_report": f"Tester error: {e}",
            "test_passed": False,
            "test_attempts": attempts,
            "messages": [AIMessage(content=f"Tester error: {e}")],
        }

E2E_PROMPT = """Generate Playwright tests that click through the React UI.

## Frontend files
{frontend_files}

## Backend files (for understanding endpoints)
{backend_files}

## Output format
Return JSON:
{{
  "files": {{ "<path>": "<file content>", ... }},
  "notes": "optional"
}}

## Rules
1. Files to generate (EXACT paths):
   - e2e/package.json         (depends on @playwright/test)
   - e2e/playwright.config.js (use process.env.BASE_URL || 'http://localhost:5173', chromium only, headless)
   - e2e/tests/app.spec.js    (at least 2 tests)
2. Tests must use `page.goto(BASE_URL)` — do NOT hardcode localhost.
3. At least one test asserts the page title or a visible heading.
4. At least one test clicks a button (e.g., the "Run Agent" button) and waits for a UI change.
5. Keep each file under 60 lines.
6. Return ONLY valid JSON, no markdown fences.
"""


def e2e_tester_node(state: SupervisorState):
    print("\n🌐 [E2E Tester]: Generating Playwright tests...")
    files = state.get("files", {}) or {}
    attempts = state.get("e2e_attempts", 0) + 1

    frontend_files = {k: v for k, v in files.items() if k.startswith("frontend/")}
    backend_files = {k: v for k, v in files.items() if k.startswith("backend/")}

    if not frontend_files:
        return {
            "e2e_report": "No frontend files to test",
            "e2e_passed": False,
            "e2e_attempts": attempts,
            "messages": [AIMessage(content="E2E: no frontend files")],
        }

    try:
        prompt = E2E_PROMPT.format(
            frontend_files=_format_files_for_prompt(frontend_files, max_chars=6000),
            backend_files=_format_files_for_prompt(backend_files, max_chars=4000),
        )
        response = llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)

        try:
            payload = json.loads(content)
            e2e_files = payload.get("files", {})
        except json.JSONDecodeError:
            return {
                "e2e_report": "LLM failed to return valid JSON for e2e tests",
                "e2e_passed": False,
                "e2e_attempts": attempts,
                "messages": [AIMessage(content="E2E: JSON parse failed")],
            }

        cleaned = {}
        for p, c in e2e_files.items():
            if isinstance(c, str):
                cleaned[sanitize_filename(p)] = c

        print(f"📄 生成 {len(cleaned)} 个 E2E 文件，启动服务中...")
        passed, output = _run_e2e(files, cleaned, timeout=300)

        report = f"=== Playwright ({'PASSED' if passed else 'FAILED'}) ===\n{output[-3000:]}"
        print(f"🌐 E2E result: {'PASSED' if passed else 'FAILED'}")

        return {
            "e2e_report": report,
            "e2e_passed": passed,
            "e2e_attempts": attempts,
            "messages": [AIMessage(content=f"E2E: {'PASSED' if passed else 'FAILED'}")],
        }
    except Exception as e:
        print(f"❌ E2E 异常: {e}")
        return {
            "e2e_report": f"E2E error: {e}",
            "e2e_passed": False,
            "e2e_attempts": attempts,
            "messages": [AIMessage(content=f"E2E error: {e}")],
        }

# ==========================================
# 5. Define Conditional Routing Edge
# ==========================================
def route_next(state) -> Literal["coder", "security", "tester", "e2e_tester", "writer", "github", "END"]:
    n = state["next_agent"]
    if n in ("coder", "security", "tester", "e2e_tester", "writer", "github"):
        return n
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
workflow.add_node("tester", tester_node)
workflow.add_node("e2e_tester", e2e_tester_node)

workflow.set_entry_point("supervisor")

workflow.add_edge("coder", "supervisor")
workflow.add_edge("security", "supervisor")
workflow.add_edge("writer", "supervisor")
workflow.add_edge("github", "supervisor")
# 边：tester / e2e_tester 完成后都回 supervisor
workflow.add_edge("tester", "supervisor")
workflow.add_edge("e2e_tester", "supervisor")

# 条件边映射加两个新目标
workflow.add_conditional_edges(
    "supervisor",
    route_next,
    {
        "coder": "coder",
        "security": "security",
        "tester": "tester",          # ← 新增
        "e2e_tester": "e2e_tester",  # ← 新增
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
        "github_owner": "homerli007-ai",
        "github_repo": "ai-coding-demo",
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

    for event in app.stream(initial_inputs, stream_mode="values"):
        pass

    print("\n✨ Supervisor Workflow Finished Successfully!")


__all__ = ["app", "workflow", "SupervisorState"]