import os
import json
from typing import Annotated, TypedDict, Sequence
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
import operator
from github import Github, InputGitAuthor

os.environ["GITHUB_TOKEN"] = ""
# ==========================================
# 1. Define the Shared State
# ==========================================
class CodeAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    task: str
    code: str
    feedback: str
    status: str          # "PASSED" or "FAILED"
    iteration_count: int # To track how many cycles have run
    # 新增
    github_owner: str
    github_repo: str
    github_base_branch: str      # 例如 "main"
    github_file_path: str        # 例如 "solution.py"
    github_pr_url: str           # 输出：生成的 PR 链接（如果有）
# ==========================================
# 2. Initialize DeepSeek LLM Client
# ==========================================
DEEPSEEK_API_KEY = ""  # <--- Replace with your actual key

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=DEEPSEEK_API_KEY,
    temperature=0.2  # Low temperature for precise code generation
)

# ==========================================
# 3. Define Agent Nodes
# ==========================================

def coder_node(state: CodeAgentState):
    """The Coder Agent writes or fixes Python code."""
    iteration = state.get("iteration_count", 0) + 1
    print(f"\n💻 [Coder Agent]: Working on code (Iteration {iteration})...")

    task = state["task"]
    feedback = state.get("feedback", "")

    if not feedback:
        prompt = f"Write a Python function for this task: {task}. Return ONLY valid Python code inside a ```python ``` code block."
    else:
        prompt = (
            f"Your previous code failed testing!\nTask: {task}\n"
            f"Tester Feedback: {feedback}\n"
            f"Please fix the code. Return ONLY valid Python code inside a ```python ``` code block."
        )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    # Extract code from markdown block if present
    if "```python" in content:
        code = content.split("```python")[1].split("```")[0].strip()
    elif "```" in content:
        code = content.split("```")[1].split("```")[0].strip()
    else:
        code = content.strip()

    print(f"📄 Generated Code:\n{code}")

    return {
        "code": code,
        "iteration_count": iteration,
        "messages": [AIMessage(content=f"Iteration {iteration} Code:\n{code}")]
    }

def tester_node(state: CodeAgentState):
    """The Tester Agent acts as a strict QA engineer."""
    print("\n🧐 [Tester Agent]: Testing and reviewing the code...")

    code = state["code"]
    task = state["task"]
    iteration = state["iteration_count"]

    # To guarantee at least 2-3 cycles of debugging for learning purposes,
    # we can artificially force failure on the 1st iteration if it's too simple!
    if iteration == 1:
        print("⚠️ [Tester Debug Hook]: Forcing a strict review failure on iteration 1 to demonstrate looping!")
        return {
            "status": "FAILED",
            "feedback": "Edge case missing: Your function does not handle negative numbers or zero correctly. Please add input validation.",
            "messages": [AIMessage(content="Tester Feedback [FAILED]: Edge cases not handled.")]
        }

    # For subsequent iterations, let LLM genuinely test it
    prompt = (
        f"You are a strict QA engineer. Task: {task}\n"
        f"Code to test:\n```python\n{code}\n```\n"
        "Check for syntax errors, logic bugs, and edge cases. "
        "If there are any bugs, reply with status FAILED and explain what's wrong. "
        "If it is completely correct and robust, reply PASSED. "
        "Reply in EXACTLY this JSON format: "
        '{"status": "PASSED", "feedback": "All tests passed."} OR '
        '{"status": "FAILED", "feedback": "Explain the bug here."}'
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content.strip()

    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)
        status = result.get("status", "FAILED")
        feedback = result.get("feedback", "Needs improvement.")
    except Exception as e:
        status = "FAILED"
        feedback = f"Failed to parse review response: {e}"

    print(f"📋 Test Result -> Status: {status}, Feedback: {feedback}")

    return {
        "status": status,
        "feedback": feedback,
        "messages": [AIMessage(content=f"Tester Feedback [{status}]: {feedback}")]
    }

def github_commit_node(state: CodeAgentState):
    """将通过测试的代码提交到 GitHub 并创建 PR"""
    print("\n🐙 [GitHub Agent]: 正在提交代码到仓库...")

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise ValueError("请设置环境变量 GITHUB_TOKEN")

    g = Github(token)
    repo = g.get_repo(f"{state['github_owner']}/{state['github_repo']}")

    # 👇 ADD THESE TWO LINES HERE
    print(f"Repo: {repo.full_name}, Base branch: {state['github_base_branch']}")
    # 1. 从基础分支创建一个新分支
    base_branch = repo.get_branch(state["github_base_branch"])
    new_branch_name = f"ai-agent/iter-{state['iteration_count']}"

    repo.create_git_ref(
        ref=f"refs/heads/{new_branch_name}",
        sha=base_branch.commit.sha
    )

    # 2. 在新分支上创建文件（或更新已有文件）
    file_path = state["github_file_path"]
    try:
        # 尝试获取已有文件（用于更新）
        existing = repo.get_contents(file_path, ref=new_branch_name)
        repo.update_file(
            path=file_path,
            message=f"AI Agent: update {file_path} (iteration {state['iteration_count']})",
            content=state["code"],
            sha=existing.sha,
            branch=new_branch_name
        )[reference:3]
    except Exception:
        # 文件不存在则创建
        repo.create_file(
            path=file_path,
            message=f"AI Agent: add {file_path} (iteration {state['iteration_count']})",
            content=state["code"],
            branch=new_branch_name
        )[reference:4]

    # 3. 创建 Pull Request
    pr = repo.create_pull(
        title=f"AI Agent 生成代码 (迭代 {state['iteration_count']})",
        body=f"由 LangGraph 多智能体循环自动生成。\n\n任务: {state['task']}",
        head=new_branch_name,
        base=state["github_base_branch"]
    )[reference:5]

    print(f"✅ PR 已创建: {pr.html_url}")

    return {
        "github_pr_url": pr.html_url,
        "messages": [AIMessage(content=f"GitHub PR: {pr.html_url}")]
    }


# ==========================================
# 4. Define Conditional Routing & Safety Limits
# ==========================================
def should_continue(state: CodeAgentState):
    """Decide whether to loop back to coder or finish."""
    status = state["status"]
    iteration = state["iteration_count"]

    # Safety limit: prevent infinite loops (max 4 cycles)
    if iteration >= 4:
        print("⚠️ Max iterations reached. Forcing exit.")
        return "end"

    if status == "PASSED":
        return "commit"
    else:
        return "retry" # Loop back to coder


# ==========================================
# 5. Assemble the Graph
# ==========================================
workflow = StateGraph(CodeAgentState)

workflow.add_node("coder", coder_node)
workflow.add_node("tester", tester_node)
workflow.add_node("github_commit", github_commit_node)

workflow.set_entry_point("coder")
workflow.add_edge("coder", "tester")

workflow.add_conditional_edges(
    "tester",
    should_continue,
    {
        "end": END,
        "retry": "coder",  # <--- This creates the loop!
        "commit": "github_commit"
    }
)

workflow.add_edge("github_commit", END)

app = workflow.compile()

# ==========================================
# 6. Run the Multi-Agent Loop
# ==========================================
if __name__ == "__main__":
    print("🚀 Multi-Agent Coding & Testing Loop Started...")

    initial_inputs = {
        "task": "Write a Python function called 'calculate_average(numbers)' that takes a list of numbers and returns their average.",
        "messages": [],
        "code": "",
        "feedback": "",
        "status": "",
        "iteration_count": 0,
        # GitHub 配置
        "github_owner": "homerLi007",
        "github_repo": "ai-coding-demo",
        "github_base_branch": "main",
        "github_file_path": "README.md",
        "github_pr_url": ""
    }

    for event in app.stream(initial_inputs, stream_mode="values"):
        pass

    print("\n✨ Multi-Agent Workflow Finished Successfully!")