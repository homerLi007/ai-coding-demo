import os
import json
from typing import Annotated, TypedDict, Sequence, Literal
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
import operator

# ==========================================
# 1. Define Advanced State
# ==========================================
class SupervisorState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    task: str
    next_agent: str          # Managed by supervisor: "coder", "security", "writer", or "FINISH"
    code: str
    security_report: str
    documentation: str

# ==========================================
# 2. Initialize DeepSeek LLM Client
# ==========================================
DEEPSEEK_API_KEY = "REMOVED_DEEPSEEK_KEY"  # <--- Replace with your actual key

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=DEEPSEEK_API_KEY,
    temperature=0.3
)

# ==========================================
# 3. Define Specialized Worker Nodes
# ==========================================

def coder_node(state: SupervisorState):
    """Worker 1: Writes the initial Python code."""
    print("\n💻 [Coder Agent]: Writing code for the task...")
    task = state["task"]

    prompt = f"Write a clean Python function for this task: {task}. Return ONLY the Python code inside a ```python ``` block."
    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content

    code = content.split("```python")[1].split("```")[0].strip() if "```python" in content else content
    print(f"📄 Code generated:\n{code}")

    return {
        "code": code,
        "messages": [AIMessage(content=f"Coder produced code:\n{code}")]
    }

def security_node(state: SupervisorState):
    """Worker 2: Audits the code for security flaws."""
    print("\n🔒 [Security Auditor]: Auditing code for vulnerabilities...")
    code = state["code"]

    prompt = f"Audit this Python code for security issues (e.g., injections, unsafe execution): \n```python\n{code}\n```\nProvide a short security audit report."
    response = llm.invoke([HumanMessage(content=prompt)])
    report = response.content
    print(f"🛡️ Security Report:\n{report}")

    return {
        "security_report": report,
        "messages": [AIMessage(content=f"Security Audit Report:\n{report}")]
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
    """The Tech Lead supervisor deciding who works next."""
    print("\n👑 [Supervisor / Tech Lead]: Evaluating project status...")

    code = state.get("code", "")
    security_report = state.get("security_report", "")
    documentation = state.get("documentation", "")

    # Construct prompt for supervisor workflow planning
    prompt = f"""
    You are a Tech Lead managing a software project. 
    Task: {state['task']}
    
    Current Project Status:
    - Code written? {"Yes" if code else "No"}
    - Security audited? {"Yes" if security_report else "No"}
    - Documentation written? {"Yes" if documentation else "No"}
    
    Your available team members are:
    1. 'coder' - Writes or fixes code.
    2. 'security' - Audits code for security.
    3. 'writer' - Writes documentation.
    4. 'FINISH' - All tasks are fully completed.
    
    Decide who should work next. Follow this workflow strictly: Coder -> Security -> Writer -> FINISH.
    Reply with EXACTLY ONE word representing the next worker or FINISH: coder, security, writer, or FINISH.
    """

    response = llm.invoke([HumanMessage(content=prompt)])
    decision = response.content.strip().lower()

    # Normalize decision
    if "coder" in decision:
        next_agent = "coder"
    elif "security" in decision:
        next_agent = "security"
    elif "writer" in decision:
        next_agent = "writer"
    else:
        next_agent = "FINISH"

    print(f"🎯 Supervisor Decision -> Next Worker: {next_agent}")
    return {"next_agent": next_agent}

# ==========================================
# 5. Define Conditional Routing Edge
# ==========================================
def route_next(state: SupervisorState) -> Literal["coder", "security", "writer", "END"]:
    next_agent = state["next_agent"]
    if next_agent == "coder":
        return "coder"
    elif next_agent == "security":
        return "security"
    elif next_agent == "writer":
        return "writer"
    else:
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

# Set entry point to supervisor
workflow.set_entry_point("supervisor")

# From workers, always return back to the supervisor to coordinate the next step
workflow.add_edge("coder", "supervisor")
workflow.add_edge("security", "supervisor")
workflow.add_edge("writer", "supervisor")

# From supervisor, conditionally route to workers or end
workflow.add_conditional_edges(
    "supervisor",
    route_next,
    {
        "coder": "coder",
        "security": "security",
        "writer": "writer",
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
        "task": "Create a secure function that takes a list of file paths and deletes them.",
        "messages": [],
        "next_agent": "",
        "code": "",
        "security_report": "",
        "documentation": ""
    }

    for event in app.stream(initial_inputs, stream_mode="values"):
        pass

    print("\n✨ Supervisor Workflow Finished Successfully!")