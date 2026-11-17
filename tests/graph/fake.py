"""Scripted chat model: real LangGraph execution, zero API calls."""

from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.runnables import RunnableLambda
from pydantic import Field


class ScriptedModel(BaseChatModel):
    responses: list[AIMessage]
    seen: list[list[BaseMessage]] = Field(default_factory=list)
    decisions: list[dict[str, Any]] = Field(
        default_factory=lambda: [{"approved": True, "reason": "Correct synthetic edit"}]
    )

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return RunnableLambda(lambda _: schema.model_validate(self.decisions.pop(0)))

    @property
    def _llm_type(self) -> str:
        return "scripted"

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self

    def _generate(self, messages: list[BaseMessage], **kwargs: Any) -> ChatResult:
        self.seen.append(list(messages))
        if not self.responses:
            raise AssertionError("Unexpected LLM call")
        return ChatResult(generations=[ChatGeneration(message=self.responses.pop(0))])


def calls(*items: tuple[str, dict[str, Any]]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {"name": name, "args": args, "id": f"call-{i}", "type": "tool_call"}
            for i, (name, args) in enumerate(items)
        ],
    )
