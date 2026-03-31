from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class NormalizedResponse:
    stop_reason: str
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)


class AnthropicProvider:
    def __init__(self, api_key: str, model: str) -> None:
        import anthropic

        self._client = anthropic.Anthropic(api_key=api_key)
        self.api_key = api_key
        self.model = model
        self._messages: list[dict] = []

    def reset(self) -> None:
        self._messages = []

    def add_task(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[dict]) -> None:
        self._messages.append(
            {
                "role": "user",
                "content": [
                    {"type": "tool_result", "tool_use_id": r["tool_use_id"], "content": r["content"]} for r in results
                ],
            }
        )

    def call(self, system: str, tools: list[dict]) -> NormalizedResponse:
        resp = self._client.messages.create(
            model=self.model,
            max_tokens=4096,
            system=system,
            tools=tools,
            messages=self._messages,
        )
        self._messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if hasattr(b, "text") and b.text)
        calls = [ToolCall(id=b.id, name=b.name, input=b.input) for b in resp.content if b.type == "tool_use"]
        return NormalizedResponse(stop_reason="tool_use" if calls else "end_turn", text=text, tool_calls=calls)

    def trim_history(self, max_pairs: int) -> None:
        _trim_pairs(self._messages, max_pairs)


class OpenAICompatProvider:
    def __init__(self, api_key: str, model: str, base_url: str) -> None:
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.fallback_model = os.getenv("OPENAI_FALLBACK_MODEL", "gpt-5-mini").strip()
        self._messages: list[dict] = []
        self._vision_history_limit = max(0, int(os.getenv("VISION_HISTORY_LIMIT", "6") or "6"))
        self._vision_message_count = 0
        self._last_vision_injected = False

    def _supports_vision(self) -> bool:
        m = self.model.lower()
        base = self.base_url.lower()
        if "openai.com" not in base:
            return False
        return m.startswith("gpt-5") or m.startswith("gpt-4.1") or m.startswith("gpt-4o")

    def reset(self) -> None:
        self._messages = []
        self._vision_message_count = 0
        self._last_vision_injected = False

    def add_task(self, text: str) -> None:
        self._messages.append({"role": "user", "content": text})

    def add_tool_results(self, results: list[dict]) -> None:
        pending_images: list[dict] = []
        self._last_vision_injected = False
        for r in results:
            content = r["content"]
            if isinstance(content, list):
                text_parts = []
                for c in content:
                    ctype = c.get("type")
                    if ctype == "text":
                        text_parts.append(c["text"])
                    elif ctype == "image":
                        src = c.get("source", {})
                        if src.get("type") == "base64" and src.get("data"):
                            pending_images.append(
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{src.get('media_type', 'image/jpeg')};base64,{src['data']}",
                                        "detail": "low",
                                    },
                                }
                            )
                content = "\n".join(text_parts)
            self._messages.append({"role": "tool", "tool_call_id": r["tool_use_id"], "content": str(content)})
        if pending_images and self._supports_vision() and self._vision_message_count < self._vision_history_limit:
            self._messages.append(
                {
                    "role": "user",
                    "content": [{"type": "text", "text": "Page screenshot for the latest browser state."}] + pending_images[:1],
                }
            )
            self._vision_message_count += 1
            self._last_vision_injected = True
            print("  [VISION] injected screenshot into OpenAI context")

    def call(self, system: str, tools: list[dict]) -> NormalizedResponse:
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "tools": _to_openai_tools(tools),
            "tool_choice": "auto",
            "messages": [{"role": "system", "content": system}] + self._messages,
        }
        try:
            resp = self._client.chat.completions.create(**payload)
        except Exception as e:
            err = str(e).lower()
            should_fallback = (
                self.model in ("gpt-5-mini", "gpt-5")
                and self.fallback_model
                and self.fallback_model != self.model
                and (
                    "model_not_found" in err
                    or "must be verified" in err
                    or "organization must be verified" in err
                    or "404" in err
                )
            )
            if not should_fallback:
                raise
            print(f"  [fallback] {self.model} unavailable, switching to {self.fallback_model}")
            self.model = self.fallback_model
            resp = self._client.chat.completions.create(**(payload | {"model": self.model}))
        msg = resp.choices[0].message
        assistant = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        self._messages.append(assistant)
        calls: list[ToolCall] = []
        for tc in msg.tool_calls or []:
            try:
                inp = json.loads(tc.function.arguments)
            except Exception:
                inp = {}
            calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))
        return NormalizedResponse(stop_reason="tool_use" if calls else "end_turn", text=msg.content or "", tool_calls=calls)

    def trim_history(self, max_pairs: int) -> None:
        _trim_pairs(self._messages, max_pairs)
        self._vision_message_count = sum(
            1
            for m in self._messages
            if m.get("role") == "user"
            and isinstance(m.get("content"), list)
            and any(part.get("type") == "image_url" for part in m["content"])
        )


def build_provider(provider: str, api_key: str, model: str, base_url: str = ""):
    p = provider.strip().lower()
    if p == "anthropic":
        return AnthropicProvider(api_key=api_key, model=model)
    if p in ("openai", "glm", "zhipu"):
        if not base_url:
            base_url = "https://api.openai.com/v1"
        return OpenAICompatProvider(api_key=api_key, model=model, base_url=base_url)
    raise ValueError(f"Unknown provider '{provider}'")


def _to_openai_tools(anthropic_tools: list[dict]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {"name": t["name"], "description": t["description"], "parameters": t["input_schema"]},
        }
        for t in anthropic_tools
    ]


def _trim_pairs(messages: list[dict], max_pairs: int) -> None:
    if len(messages) <= max_pairs * 2 + 1:
        return
    first = messages[0]
    rest = messages[1:]
    boundaries = [i for i, m in enumerate(rest) if m["role"] == "assistant"]
    if len(boundaries) <= max_pairs:
        return
    cut = boundaries[-max_pairs]
    kept = rest[cut:]
    messages.clear()
    messages.append(first)
    messages.append({"role": "user", "content": "[Context trimmed. Continue from current browser state.]"})
    messages.append({"role": "assistant", "content": "Understood."})
    messages.extend(kept)
