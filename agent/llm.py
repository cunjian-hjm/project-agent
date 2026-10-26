"""LLM initialization."""
import os

from dotenv import load_dotenv
from langchain.chat_models import init_chat_model

load_dotenv(verbose=True,override=True)

llm = init_chat_model(
    model=os.getenv("LLM_QWEN_FLASH"),
    model_provider="openai",
    api_key=os.getenv("DASHSCOPE_API_KEY"),
    base_url=os.getenv("DASHSCOPE_BASE_URL")
)