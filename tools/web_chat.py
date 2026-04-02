"""
Async web chat session for investment criteria extraction.

Unlike tools/chat_intake.py (CLI/blocking readline), this module is designed
for HTTP request/response: conversation state lives server-side, keyed by
session ID, and each HTTP call advances the conversation by one turn.
"""
import os

from anthropic import AsyncAnthropic

from tools.chat_intake import _SYSTEM_PROMPT, _TOOL_DEF, _build_config
from tools.models import InvestmentConfig

_MODEL = "claude-sonnet-4-6"


class ChatSession:
    """Server-side conversation state for one user session."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.extracted: dict | None = None  # last criteria extracted by Claude

    async def send(self, user_text: str) -> dict:
        """
        Advance the conversation by one user turn.

        Returns:
            text:      Claude's response text (shown in the chat UI)
            criteria:  extracted InvestmentConfig fields if tool was called, else None
            confirmed: True once criteria have been extracted at least once
        """
        client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        self.messages.append({"role": "user", "content": user_text})

        response = await client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=_SYSTEM_PROMPT,
            tools=[_TOOL_DEF],
            messages=self.messages,
        )

        texts: list[str] = []
        tool_input: dict | None = None
        tool_use_id: str | None = None

        for block in response.content:
            if block.type == "text":
                texts.append(block.text)
            elif block.type == "tool_use" and block.name == "set_investment_criteria":
                tool_input = block.input
                tool_use_id = block.id

        self.messages.append({"role": "assistant", "content": response.content})

        if tool_input:
            self.extracted = tool_input
            # Return tool result so Claude can write the confirmation summary
            self.messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": "Criteria recorded.",
                }],
            })
            confirm = await client.messages.create(
                model=_MODEL,
                max_tokens=1024,
                system=_SYSTEM_PROMPT,
                tools=[_TOOL_DEF],
                messages=self.messages,
            )
            confirm_texts = [b.text for b in confirm.content if b.type == "text"]
            self.messages.append({"role": "assistant", "content": confirm.content})

            return {
                "text": "\n".join(confirm_texts) or "\n".join(texts),
                "criteria": self.extracted,
                "confirmed": True,
            }

        return {
            "text": "\n".join(texts),
            "criteria": self.extracted,
            "confirmed": self.extracted is not None,
        }

    def build_config(self, base_config: InvestmentConfig) -> InvestmentConfig | None:
        """Merge extracted criteria into the base pipeline config."""
        if not self.extracted:
            return None
        return _build_config(self.extracted, base_config)