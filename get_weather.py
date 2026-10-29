import json
import os
from openai import OpenAI

# 1. 初始化大模型客户端（假设使用 OpenAI 或兼容的 API）
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ==========================================
# 2. 定义我们的“工具箱”（真实的 Python 函数）
# ==========================================
def get_current_weather(location: str):
    """模拟一个获取天气的工具"""
    # 真实场景中这里会去请求第三方天气 API
    if "北京" in location:
        return json.dumps({"location": "北京", "temperature": "22℃", "condition": "晴朗"})
    elif "上海" in location:
        return json.dumps({"location": "上海", "temperature": "25℃", "condition": "小雨"})
    else:
        return json.dumps({"location": location, "temperature": "未知", "condition": "暂无数据"})

# 将工具注册到一个字典里，方便根据名字动态调用
available_tools = {
    "get_current_weather": get_current_weather
}

# 告诉大模型我们有哪些工具（OpenAI 的标准 tools 结构）
tools_declaration = [
    {
        "type": "function",
        "function": {
            "name": "get_current_weather",
            "description": "获取指定城市当前的天气情况",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "城市名称，例如：北京、上海"}
                },
                "required": ["location"]
            }
        }
    }
]

# ==========================================
# 3. 核心：手写 ReAct 循环 (The Agent Loop)
# ==========================================
def run_agent_loop(user_prompt):
    # 初始化消息历史记录（Memory）
    messages = [
        {"role": "system", "content": "你是一个智能助手。你可以使用工具来查询实时信息。"},
        {"role": "user", "content": user_prompt}
    ]

    print(f"🤖 用户任务: {user_prompt}\n" + "="*40)

    # 设置最大循环次数，防止死循环
    max_turns = 5
    for turn in range(max_turns):
        print(f"\n--- [第 {turn + 1} 轮思考与行动循环] ---")

        # 步骤 A: 让大模型思考 (Reasoning)
        response = client.chat.completions.create(
            model="gpt-4o",  # 或者其他支持 function calling 的模型
            messages=messages,
            tools=tools_declaration,
            tool_choice="auto" # 让模型自己决定是否调用工具
        )

        response_message = response.choices[0].message

        # 把大模型的回复追加到历史记录中（保持记忆）
        messages.append(response_message)

        # 步骤 B: 检查大模型是想直接回答，还是想调用工具 (Acting)
        if response_message.tool_calls:
            # ─── 分支 1：大模型决定调用工具 ───
            print("🧠 大模型思考结果: 决定调用工具！")

            for tool_call in response_message.tool_calls:
                function_name = tool_call.function.name
                function_args = json.loads(tool_call.function.arguments)

                print(f"   🛠️ 动作 (Action): 准备调用工具 [{function_name}]")
                print(f"   📦 参数 (Arguments): {function_args}")

                # 执行本地对应的 Python 函数
                if function_name in available_tools:
                    tool_function = available_tools[function_name]
                    # 动态执行函数
                    tool_result = tool_function(**function_args)
                    print(f"   👁️ 观察结果 (Observation): {tool_result}")

                    # 将工具的返回结果包装成 tool 角色，塞回消息历史
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": function_name,
                        "content": tool_result
                    })
                else:
                    print(f"   ❌ 错误: 未找到工具 {function_name}")

            # 循环继续：带着工具的返回结果，进入下一轮 LLM 思考
            continue

        else:
            # ─── 分支 2：大模型觉得不需要工具，或者已经得到了答案 ───
            print("🏁 大模型思考结果: 任务完成，给出最终回答！")
            final_answer = response_message.content
            print(f"\n✨ 最终答案:\n{final_answer}")
            return final_answer

    print("⚠️ 警告: 达到最大循环次数，强制终止。")

# ==========================================
# 4. 运行测试
# ==========================================
if __name__ == "__main__":
    run_agent_loop("请帮我查一下北京今天的天气怎么样？如果温度低于25度，顺便告诉我需要带外套吗？")