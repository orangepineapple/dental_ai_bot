from src.exceptions import Conflict


class LLMUnavailable(Exception):
    """The model provider could not be reached or is not configured."""


class ConversationFull(Conflict):
    DETAIL = "This conversation has reached its message limit. Please start a new chat."
