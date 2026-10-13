from functools import lru_cache
from typing import Annotated

from fastapi import Depends

from src.chat.config import ChatConfig, chat_settings
from src.chat.llm import LLMClient, OpenAICompatibleClient
from src.tools.registry import ToolRegistry, tool_registry


@lru_cache
def _llm_client() -> OpenAICompatibleClient:
    return OpenAICompatibleClient(chat_settings)


def get_llm_client() -> LLMClient:
    return _llm_client()


def get_tool_registry() -> ToolRegistry:
    return tool_registry


def get_chat_config() -> ChatConfig:
    return chat_settings


LLM = Annotated[LLMClient, Depends(get_llm_client)]
Registry = Annotated[ToolRegistry, Depends(get_tool_registry)]
Config = Annotated[ChatConfig, Depends(get_chat_config)]
