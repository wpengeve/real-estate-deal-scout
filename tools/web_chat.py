"""
Async web chat session for investment criteria extraction.

Unlike tools/chat_intake.py (CLI/blocking readline), this module is designed
for HTTP request/response: conversation state lives server-side, keyed by
session ID, and each HTTP call advances the conversation by one turn.
"""
import os

from anthropic import AsyncAnthropic

from tools.chat_intake import _SYSTEM_PROMPT, _TOOL_DEF, _build_config
from tools.models import InvestmentConfig, Shortlist

_MODEL = "claude-sonnet-4-6"

_QA_SYSTEM_PROMPT_TEMPLATE = """\
You are a real estate investment advisor. The user has just reviewed their Deal Scout results.

RESULTS CONTEXT:
{shortlist}

Your job:
- Answer questions about these specific properties. Be concrete: cite actual numbers \
(cap rate, cash flow, price, walk score, risk flags, narrative).
- If asked to compare two properties, do it side by side with key metrics.
- If asked why a property ranked where it did, explain using the narrative and financials.
- If the user wants to search for different properties or a new market, help them — \
collect new criteria and call set_investment_criteria as usual.
- Be concise. Investors are busy.\
"""


def _shortlist_summary(shortlist: Shortlist) -> str:
    """Compact text summary of the shortlist for use as system prompt context."""
    lines = [f"Market: {shortlist.market} — {len(shortlist.deals)} deals"]
    for d in shortlist.deals:
        price = f"${d.price:,.0f}" if d.price else "—"
        beds = f"{d.beds}bd" if d.beds else "—"
        baths = f"/{d.baths:g}ba" if d.baths else ""
        cap = f"{d.cap_rate*100:.2f}%" if d.cap_rate is not None else "—"
        cf_val = d.monthly_cashflow
        if cf_val is not None:
            cf = f"{'+'if cf_val>=0 else ''}${round(cf_val):,}/mo"
        else:
            cf = "—"
        ws = str(d.walk_score) if d.walk_score is not None else "—"
        verdict = d.narrative.split("\n")[0][:80] if d.narrative else "—"
        lines.append(
            f"#{d.rank} {d.address} — {price} | {beds}{baths} | "
            f"Cap: {cap} | CF: {cf} | Walk: {ws} | {d.risk_level} risk — {verdict}"
        )
    return "\n".join(lines)


class ChatSession:
    """Server-side conversation state for one user session."""

    def __init__(
        self,
        messages: list[dict] | None = None,
        extracted: dict | None = None,
    ) -> None:
        self.messages: list[dict] = messages or []
        self.extracted: dict | None = extracted
        self.shortlist_context: str | None = None  # set after pipeline completes

    def set_shortlist(self, shortlist: Shortlist) -> None:
        """Attach shortlist results so follow-up questions have context."""
        self.shortlist_context = _shortlist_summary(shortlist)

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

        if self.shortlist_context:
            system = _QA_SYSTEM_PROMPT_TEMPLATE.format(shortlist=self.shortlist_context)
        else:
            system = _SYSTEM_PROMPT

        response = await client.messages.create(
            model=_MODEL,
            max_tokens=1024,
            system=system,
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
                system=system,
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

    def build_config(
        self,
        base_config: InvestmentConfig,
        scraperapi_search_urls: list[str] | None = None,
    ) -> InvestmentConfig | None:
        """Merge extracted criteria into the base pipeline config."""
        if not self.extracted:
            return None
        extracted = {**self.extracted}
        if scraperapi_search_urls:
            extracted["scraperapi_search_urls"] = scraperapi_search_urls
        return _build_config(extracted, base_config)