import os
import json
from typing import Annotated, TypedDict, Sequence
from langchain_core.messages import BaseMessage, HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode
import operator

# ==========================================
# 1. Define Tools
# ==========================================
def get_current_weather(location: str):
    """Get the current weather for a given city."""
    print(f"\n >>> [DEBUG LOG] Executed local Python function: get_current_weather, argument location={location} <<< \n")
    if "Beijing" in location or "北京" in location:
        return json.dumps({"location": "Beijing", "temperature": "22°C", "condition": "Sunny"})
    elif "Shanghai" in location or "上海" in location:
        return json.dumps({"location": "Shanghai", "temperature": "25°C", "condition": "Light Rain"})
    else:
        return json.dumps({"location": location, "temperature": "Unknown", "condition": "No data available"})

# Register tools in a list
tools = [get_current_weather]

# ==========================================
# 2. Define LangGraph Global State
# ==========================================
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], operator.add]

# ==========================================
# 3. Initialize DeepSeek LLM and Bind Tools
# ==========================================
# Note: DeepSeek provides an OpenAI-compatible API, so we can use ChatOpenAI
# by specifying the deepseek base_url and deepseek-chat model name.
model = ChatOpenAI(
    model="deepseek-chat",
    base_url="https://api.deepseek.com",
    api_key="REMOVED_DEEPSEEK_KEY",  # Make sure to set DEEPSEEK_API_KEY in your environment
    temperature=0
).bind_tools(tools)

# ==========================================
# 4. Define Nodes and Conditional Edges
# ==========================================
def call_model(state: AgentState):
    """LLM reasoning node"""
    messages = state["messages"]
    response = model.invoke(messages)
    return {"messages": [response]}

# Official built-in tool execution node
tool_node = ToolNode(tools=tools)

def should_continue(state: AgentState):
    """Determine whether to continue calling tools or finish the task."""
    messages = state["messages"]
    last_message = messages[-1]

    if last_message.tool_calls:
        return "tools"
    return END

# ==========================================
# 5. Assemble the Workflow Graph
# ==========================================
workflow = StateGraph(AgentState)

workflow.add_node("agent", call_model)
workflow.add_node("tools", tool_node)

workflow.set_entry_point("agent")

workflow.add_conditional_edges(
    "agent",
    should_continue,
    {
        "tools": "tools",
        END: END
    }
)

workflow.add_edge("tools", "agent")

# Compile into an executable app
app = workflow.compile()

# ==========================================
# 6. Local Execution & Test Entry Point
# ==========================================
if __name__ == "__main__":
    print("🚀 Agent started with DeepSeek...")

    # Construct user input
    inputs = {"messages": [HumanMessage(content="How is the weather in Beijing today? If the temperature is below 25 degrees, do I need a jacket?")]}

    # Execute the graph stream
    for event in app.stream(inputs, stream_mode="values"):
        latest_msg = event["messages"][-1]
        print(f"[{latest_msg.type.upper()}] {latest_msg.content}")
        if hasattr(latest_msg, "tool_calls") and latest_msg.tool_calls:
            print(f"  --> Triggered tool calls: {latest_msg.tool_calls}")