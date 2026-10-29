import os
from dotenv import load_dotenv
from browser_use import Agent
from langchain_openai import ChatOpenAI

load_dotenv() # 读取 .env 文件中的环境变量

llm = ChatOpenAI(
    model="deepseek-chat",          # 或 "deepseek-reasoner"
    base_url="https://api.deepseek.com/v1", # DeepSeek 的 OpenAI 兼容端点
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    temperature=0.0,                # 浏览器自动化任务建议设为较低值以保证确定性
)

agent = Agent(
    task="你的浏览器任务描述...",
    llm=llm,                        # 将 your_llm 替换为上面创建的 llm 对象
)