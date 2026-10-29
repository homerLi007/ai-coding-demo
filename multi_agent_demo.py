import os
import json
from typing import Annotated, TypedDict, Sequence
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
import operator

# ==========================================
# 1. Define the Shared State
# ==========================================
class MultiAgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]
    topic: str
    draft: str
    feedback: str
    status: str  # "APPROVED" or "REVISE"

# ==========================================
# 2. Initialize DeepSeek LLM Client
# ==========================================
# Replace with your actual DeepSeek API key
DEEPSEEK_API_KEY = "REMOVED_DEEPSEEK_KEY"

llm = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key=DEEPSEEK_API_KEY,
    temperature=0.7
)

# ==========================================
# 3. Define Agent Nodes (The Workers)
# ==========================================

def writer_node(state: MultiAgentState):
    """The Writer Agent creates or revises the draft."""
    print("\n🤖 [Writer Agent]: Working on the draft...")

    topic = state["topic"]
    feedback = state.get("feedback", "")

    if not feedback:
        prompt = f"Write a short, engaging 3-sentence joke about the topic: {topic}"
    else:
        prompt = f"Revise your previous draft based on this reviewer feedback: '{feedback}'. Topic: {topic}"

    response = llm.invoke([HumanMessage(content=prompt)])
    draft = response.content
    print(f"✍️ Draft created:\n{draft}")

    return {
        "draft": draft,
        "messages": [AIMessage(content=f"Writer Draft: {draft}")]
    }

def reviewer_node(state: MultiAgentState):
    """The Reviewer Agent evaluates the draft."""
    print("\n🧐 [Reviewer Agent]: Reviewing the draft...")

    draft = state["draft"]

    prompt = (
        f"Review the following draft:\n'''\n{draft}\n'''\n"
        "Is this funny and high quality? "
        "Reply in EXACTLY this JSON format: "
        '{"status": "APPROVED", "feedback": "Looks great!"} OR '
        '{"status": "REVISE", "feedback": "Make it punchier."}'
    )

    response = llm.invoke([HumanMessage(content=prompt)])
    content = response.content.strip()

    # Simple parsing of the reviewer's JSON response
    try:
        # Clean markdown code blocks if present
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0].strip()
        elif "```" in content:
            content = content.split("```")[1].split("```")[0].strip()

        result = json.loads(content)
        status = result.get("status", "APPROVED")
        feedback = result.get("feedback", "Approved")
    except Exception as e:
        print(f"⚠️ Failed to parse JSON, defaulting to APPROVED. Error: {e}")
        status = "APPROVED"
        feedback = "Looks good."

    print(f"📋 Review Result -> Status: {status}, Feedback: {feedback}")

    return {
        "status": status,
        "feedback": feedback,
        "messages": [AIMessage(content=f"Reviewer Feedback [{status}]: {feedback}")]
    }

# ==========================================
# 4. Define Conditional Routing Edge
# ==========================================
def should_continue(state: MultiAgentState):
    """Decide whether to finish or send back to writer for revision."""
    if state["status"] == "APPROVED":
        return "end"
    else:
        return "revise"

# ==========================================
# 5. Assemble the Multi-Agent Graph
# ==========================================
workflow = StateGraph(MultiAgentState)

# Add nodes
workflow.add_node("writer", writer_node)
workflow.add_node("reviewer", reviewer_node)

# Set entry point
workflow.set_entry_point("writer")

# Linear edge from writer to reviewer
workflow.add_edge("writer", "reviewer")

# Conditional edge from reviewer
workflow.add_conditional_edges(
    "reviewer",
    should_continue,
    {
        "end": END,
        "revise": "writer"  # Loop back to writer if revision is needed
    }
)

# Compile graph
multi_agent_app = workflow.compile()

# ==========================================
# 6. Run the Multi-Agent System
# ==========================================
if __name__ == "__main__":
    print("🚀 Multi-Agent Workflow Started...")

    initial_inputs = {
        "topic": "Python programmers and coffee",
        "messages": [],
        "draft": "",
        "feedback": "",
        "status": ""
    }

    # Stream the multi-agent execution
    for event in multi_agent_app.stream(initial_inputs, stream_mode="values"):
        # You can inspect state updates here if needed
        pass

    print("\n✨ Workflow Finished Successfully!")