"""
Autonomous browser agent — universal orchestrator with role-based sub-agent delegation.

Architecture:
- Main agent: full orchestration, can delegate to specialized sub-agents.
- Sub-agent roles: navigator | extractor | interactor | verifier
  Each role gets a focused system prompt and a restricted tool set appropriate
  for its job. This replaces the previous formal delegation with real specialization.

Design principles:
- No domain-specific hardcoding (no e-commerce, banking, or other vertical bias).
- A11y-first: prefers click_by_role_name after read_accessibility_tree for ambiguous targets.
- Observe → Act → Verify loop enforced at both code and prompt level.
"""
import asyncio
import json
import re
import sys
from collections import deque
from typing import Any, Awaitable, Callable, Optional

from agent.browser import BrowserController
from agent.page import PageAnalyzer
from agent.providers import AnthropicProvider, NormalizedResponse, OpenAICompatProvider
from agent.tools import TOOL_DEFINITIONS, tools_for_role

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

AnyProvider = AnthropicProvider | OpenAICompatProvider
MAX_TURNS = 60
MAX_HISTORY_PAIRS = 12

# ---------------------------------------------------------------------------
# System prompts — one per agent role
# ---------------------------------------------------------------------------

_ORCHESTRATOR_PROMPT = """You are an autonomous AI browser agent capable of completing any web-based task.

Workflow (strict loop — never skip steps):
1. Observe: call get_page_state.
   It returns three things in one call:
   - DOM element list with numeric IDs (for click_element / type_text / select_dropdown)
   - ACCESSIBILITY TREE with role/name pairs (for click_by_role_name — more reliable)
   - Screenshot and page text
2. Plan: if the target is still ambiguous after step 1, call get_action_candidates or query_dom.
   For complex or dynamically loaded widgets, call read_accessibility_tree for the full tree.
3. Act: perform exactly one best action.
4. Verify: call get_page_state again and confirm the expected change occurred.
5. Repeat until done, then call task_complete.

Element targeting strategy (in priority order):
1. click_by_role_name — use the role/name from the ACCESSIBILITY TREE in get_page_state.
   Most reliable: works regardless of CSS class changes, obfuscated IDs, or layout shifts.
2. click_best_match — use when role/name is not in the a11y tree (non-accessible pages).
3. click_element — last resort fallback by numeric DOM ID.

Delegation:
- Use delegate_subtask to hand off focused sub-tasks to specialized agents.
  Roles: navigator (find pages), extractor (read data), interactor (forms/clicks), verifier (confirm state).
- Delegate verification steps to 'verifier' to keep the main flow clean.
- Delegate pure data extraction to 'extractor' (it is read-only and efficient).

Safety rules:
- Never assume an action succeeded without calling get_page_state to verify.
- Use confirm_destructive_action before irreversible actions (purchases, deletions, form submission with real consequences).
- Use ask_user only when information is genuinely unavailable and cannot be inferred.
"""

_ROLE_PROMPTS: dict[str, str] = {
    "navigator": """You are a Navigation specialist sub-agent.
Your sole job: reach the target page or confirm it does not exist.
Strategy:
- Call get_page_state first — it includes ACCESSIBILITY TREE with all link/button names.
- Use click_by_role_name with role='link' or role='button' + the name from the a11y tree.
- Use navigate_to_url for direct URL navigation.
- For menus that require hover, use hover_element then re-call get_page_state.
- Stop and call task_complete as soon as you reach the target page.
- Do NOT fill forms or interact beyond what navigation requires.
""",

    "extractor": """You are a Data Extraction specialist sub-agent. You are READ-ONLY.
Your sole job: read and return information from the current page.
Strategy:
- Call get_page_state first — it includes page text, DOM elements, and the a11y tree.
  The a11y tree reveals checkbox states, input values, and widget states that raw text misses.
- Use extract_page_text for full page text content.
- Use read_accessibility_tree if you need more detail than what get_page_state provides.
- Scroll to reveal hidden content if needed.
- Do NOT click any interactive elements or modify page state.
- Return all extracted data clearly in task_complete summary.
""",

    "interactor": """You are an Interaction specialist sub-agent.
Your sole job: complete a specific form interaction, widget operation, or click sequence.
Strategy:
- Call get_page_state before each step — the ACCESSIBILITY TREE in the response
  gives you role/name pairs for reliable click_by_role_name targeting.
- Prefer click_by_role_name over click_element (more stable across page re-renders).
- Use hover_element when a dropdown or tooltip must appear before clicking.
- After every action, call get_page_state to verify the state changed as expected.
- If an element is not found after 2 attempts, try the other selector approach
  (if you used click_best_match, switch to click_by_role_name, and vice versa).
""",

    "verifier": """You are a Verification specialist sub-agent. You are READ-ONLY.
Your sole job: confirm whether an expected state exists on the current page.
Strategy:
- Call get_page_state — it includes the ACCESSIBILITY TREE which shows checkbox states,
  expanded/collapsed states, and current input values alongside page text.
- Cross-check both the DOM element list and the a11y tree before concluding.
- Do NOT click, type, navigate, or modify any page state.
- Report clearly: what exact evidence you found, and whether the expected state
  is CONFIRMED or NOT FOUND.
""",
}

# ---------------------------------------------------------------------------
# Role detection — regex with Unicode word boundaries + negation guard
# ---------------------------------------------------------------------------
# Each pattern is compiled once with IGNORECASE | UNICODE.
# \b works correctly for Cyrillic because Python's \w matches Unicode letters.
# Multi-word phrases use \s+ between words to avoid false positives.

_ROLE_PATTERNS: list[tuple[str, list[re.Pattern]]] = [
    ("verifier", [re.compile(p, re.I | re.U) for p in [
        r'\bverify\b', r'\bcheck\b', r'\bconfirm\b', r'\bassert\b',
        r'\bvalidate\b', r'\bensure\b', r'\bis\s+it\b', r'\bdid\s+it\b',
        r'\bпроверь\b', r'\bубедись\b', r'\bудостоверься\b', r'\bподтверди\b',
    ]]),
    ("extractor", [re.compile(p, re.I | re.U) for p in [
        r'\bread\b', r'\bextract\b', r'\bfind\s+out\b', r'\bwhat\s+is\b',
        r'\bwhat\s+are\b', r'\bhow\s+many\b', r'\bget\s+the\b', r'\bscrape\b',
        r'\bпрочитай\b', r'\bизвлеки\b', r'\bузнай\b', r'\bсколько\b',
        r'\bчто\s+за\b', r'\bполучи\b',
    ]]),
    ("navigator", [re.compile(p, re.I | re.U) for p in [
        r'\bnavigate\b', r'\bgo\s+to\b', r'\bopen\b', r'\bvisit\b',
        r'\bfind\s+page\b', r'\bsearch\s+for\b',
        r'\bперейди\b', r'\bоткрой\b', r'\bнайди\s+страницу\b', r'\bзайди\b',
    ]]),
    ("interactor", [re.compile(p, re.I | re.U) for p in [
        r'\bfill\b', r'\btype\b', r'\bsubmit\b', r'\bselect\b', r'\bchoose\b',
        r'\bclick\b', r'\buncheck\b',
        r'\bзаполни\b', r'\bвпиши\b', r'\bнажми\b', r'\bвыбери\b',
        r'\bкликни\b', r'\bпоставь\s+галочку\b',
    ]]),
]

# Words that negate the following keyword (within ~2 words)
_NEGATION_RE = re.compile(
    r'\b(?:не|без|нет|no|not|without|don\'?t|never|никогда)\b\s+(?:\w+\s+){0,2}$',
    re.I | re.U,
)


def _detect_role(task: str) -> str:
    """
    Detect the best sub-agent role for a task string.

    Uses compiled regex with word boundaries so:
    - "checkbox" doesn't match "check"
    - "нажимай" in "не нажимай" is rejected by negation detection
    Role priority: verifier > extractor > navigator > interactor
    """
    for role, patterns in _ROLE_PATTERNS:
        for pat in patterns:
            m = pat.search(task)
            if not m:
                continue
            # Check if preceded by a negation word
            preceding = task[: m.start()]
            if _NEGATION_RE.search(preceding):
                continue  # negated — skip this match
            return role
    return "interactor"  # safe default for mixed/complex tasks


# ---------------------------------------------------------------------------
# Main agent class
# ---------------------------------------------------------------------------

class AIAgent:
    def __init__(
        self,
        browser: BrowserController,
        analyzer: PageAnalyzer,
        provider: AnyProvider,
        console: Any,
        *,
        role: str = "orchestrator",
        max_turns: int = MAX_TURNS,
    ):
        self.browser = browser
        self.analyzer = analyzer
        self.provider = provider
        self.console = console
        self.role = role
        self.max_turns = max_turns
        self._user_input_cb: Optional[Callable[[str], Awaitable[str]]] = None
        self._confirm_cb: Optional[Callable[[str], Awaitable[bool]]] = None
        self._action_counter = 0
        self._recent_actions: deque[str] = deque(maxlen=8)
        self._current_task: str = ""

    def set_callbacks(
        self,
        user_input: Callable[[str], Awaitable[str]],
        confirm: Callable[[str], Awaitable[bool]],
    ) -> None:
        self._user_input_cb = user_input
        self._confirm_cb = confirm

    def _system_prompt(self) -> str:
        return _ROLE_PROMPTS.get(self.role, _ORCHESTRATOR_PROMPT)

    def _tool_definitions(self) -> list[dict]:
        if self.role in _ROLE_PROMPTS:
            return tools_for_role(self.role)
        return TOOL_DEFINITIONS  # orchestrator gets all tools

    async def run_task(self, task: str) -> str:
        self.provider.reset()
        self.provider.add_task(f"Task: {task}")
        self._recent_actions.clear()
        self._current_task = task

        prefix = f"[{self.role}] " if self.role != "orchestrator" else ""
        self.console.print(f"\n{prefix}Starting: {task}\n")

        tools = self._tool_definitions()
        result = "(task did not complete within turn limit)"
        consecutive_errors = 0

        for turn in range(1, self.max_turns + 1):
            try:
                response: NormalizedResponse = await asyncio.to_thread(
                    self.provider.call, self._system_prompt(), tools
                )
            except Exception as e:
                err = str(e).lower()
                if "insufficient_quota" in err or "quota" in err:
                    self.console.print("LLM quota exceeded. Update .env key/provider.")
                    break
                if "rate" in err:
                    self.console.print("Rate limited. Waiting 20s...")
                    await asyncio.sleep(20)
                    continue
                self.console.print(f"LLM error: {e}")
                break

            if response.text:
                short = response.text.strip().replace("\n", " ")
                self.console.print(f"[{self.role} turn {turn}] {short[:400]}")

            if response.stop_reason == "end_turn":
                result = response.text or "(no summary)"
                break

            tool_results: list[dict] = []
            task_done = False
            for tc in response.tool_calls:
                raw = await self._execute_tool(tc.name, tc.input)
                raw_text = raw if isinstance(raw, str) else ""
                action_sig = f"{tc.name}:{json.dumps(tc.input, sort_keys=True, ensure_ascii=False)}"
                self._recent_actions.append(action_sig)
                repeat_count = sum(1 for item in self._recent_actions if item == action_sig)

                if isinstance(raw_text, str) and raw_text.startswith("ERROR"):
                    consecutive_errors += 1
                else:
                    consecutive_errors = 0

                if tc.name == "task_complete":
                    result = tc.input.get("summary", "Done.")
                    task_done = True
                    tool_results.append({"tool_use_id": tc.id, "content": "Acknowledged."})
                else:
                    out_content = raw if isinstance(raw, list) else str(raw)
                    if isinstance(out_content, str) and out_content.startswith("ERROR") and consecutive_errors >= 2:
                        out_content += (
                            "\nHINT: Try get_page_state, read_accessibility_tree, query_dom, "
                            "scroll_page, wait_for_page, or a different selector strategy."
                        )
                    if repeat_count >= 3:
                        out_content += (
                            "\nLOOP WARNING: Same action repeated 3 times. "
                            "Re-observe with get_page_state or read_accessibility_tree and choose a different approach."
                        )
                    tool_results.append({"tool_use_id": tc.id, "content": out_content})

            self.provider.add_tool_results(tool_results)
            self.provider.trim_history(MAX_HISTORY_PAIRS)

            if task_done:
                self.console.print(f"Task complete\n{result}")
                break

        return result

    # ------------------------------------------------------------------
    # Tool dispatch
    # ------------------------------------------------------------------

    async def _execute_tool(self, name: str, inp: dict) -> Any:
        self.console.print(f"  -> {name} {json.dumps(inp, ensure_ascii=False)[:120]}")
        try:
            match name:
                case "get_page_state":
                    return await self._t_get_page_state()
                case "read_accessibility_tree":
                    return await self._t_read_a11y()
                case "describe_visible_region":
                    return await self.analyzer.describe_visible_region(int(inp.get("max_chars", 900)))
                case "get_action_candidates":
                    return await self._t_get_action_candidates(
                        inp["query"],
                        str(inp.get("action", "click")),
                        int(inp.get("max_results", 8)),
                    )
                case "extract_page_text":
                    return await self.analyzer.get_full_text(max(500, min(int(inp.get("max_chars", 5000)), 15_000)))
                case "query_dom":
                    return await self._t_query_dom(inp["query"], int(inp.get("max_results", 15)))
                case "click_best_match":
                    return await self._t_click_best_match(inp["query"])
                case "click_by_role_name":
                    return await self._t_click_by_role_name(
                        inp["role"], inp["name"], bool(inp.get("exact", False))
                    )
                case "type_by_role_name":
                    return await self._t_type_by_role_name(
                        inp["role"], inp["name"], inp["text"], bool(inp.get("exact", False))
                    )
                case "type_into_best_match":
                    return await self._t_type_best_match(inp["query"], inp["text"])
                case "take_screenshot":
                    path = await self.browser.save_screenshot(bool(inp.get("full_page", False)))
                    self.console.print(f"  [SCREENSHOT] {path}")
                    return f"OK: screenshot saved to {path}"
                case "click_element":
                    return await self._t_click(inp["element_id"])
                case "hover_element":
                    return await self._t_hover(inp["element_id"])
                case "type_text":
                    return await self._t_type(inp["element_id"], inp["text"])
                case "select_dropdown":
                    return await self._t_select(inp["element_id"], inp["value"])
                case "press_key":
                    result = await self.browser.press_key(inp["key"])
                    await asyncio.sleep(1.0)
                    shot = await self.browser.save_screenshot(False)
                    self.console.print(f"  [SCREENSHOT] after {inp['key']} -> {shot}")
                    return f"{result}\nCheckpoint screenshot: {shot}"
                case "navigate_to_url":
                    nav = await self.browser.navigate(inp["url"])
                    if isinstance(nav, str) and nav.startswith("OK"):
                        shot = await self.browser.save_screenshot(False)
                        self.console.print(f"  [SCREENSHOT] after navigate -> {shot}")
                        return f"{nav}\nCheckpoint screenshot: {shot}"
                    return nav
                case "go_back":
                    result = await self.browser.go_back()
                    shot = await self.browser.save_screenshot(False)
                    self.console.print(f"  [SCREENSHOT] after go_back -> {shot}")
                    return f"{result}\nCheckpoint screenshot: {shot}"
                case "scroll_page":
                    return await self.browser.scroll(inp["direction"], inp.get("pixels", 600))
                case "wait_for_page":
                    return await self.browser.wait(inp.get("seconds", 2))
                case "ask_user":
                    return await self._t_ask_user(inp["question"])
                case "confirm_destructive_action":
                    return await self._t_confirm(inp["action_description"])
                case "delegate_subtask":
                    return await self._t_delegate(inp["task"], inp["context"], inp.get("role", ""))
                case "task_complete":
                    return "done"
                case _:
                    return f"ERROR: unknown tool '{name}'"
        except KeyError as e:
            return f"ERROR: missing parameter {e}"
        except Exception as e:
            return f"ERROR executing {name}: {e}"
        finally:
            if name in {
                "navigate_to_url", "click_element", "type_text", "select_dropdown",
                "click_best_match", "click_by_role_name", "type_by_role_name",
                "type_into_best_match", "hover_element", "go_back",
            }:
                self._action_counter += 1

    # ------------------------------------------------------------------
    # Individual tool implementations
    # ------------------------------------------------------------------

    async def _t_get_page_state(self) -> list:
        # Run DOM capture and a11y snapshot concurrently to avoid extra latency.
        state_task = asyncio.create_task(self.analyzer.capture())
        a11y_task = asyncio.create_task(self.browser.accessibility_snapshot())
        state, a11y_raw = await asyncio.gather(state_task, a11y_task)

        self.browser.store_elements(state.elements)
        screenshot = await self.browser.screenshot_b64()

        # Compact a11y section: always included so the LLM can use
        # click_by_role_name without a separate round-trip.
        A11Y_LIMIT = 1800
        a11y_body = a11y_raw[:A11Y_LIMIT]
        a11y_tail = "\n[...truncated — call read_accessibility_tree for full tree]" if len(a11y_raw) > A11Y_LIMIT else ""
        a11y_section = (
            "\nACCESSIBILITY TREE (role + name — use with click_by_role_name):\n"
            + a11y_body
            + a11y_tail
        )

        summary = (
            f"URL: {state.url}\n"
            f"Title: {state.title}\n\n"
            f"Element count: {len(state.elements)}\n\n"
            "INTERACTIVE ELEMENTS (use IDs for click_element / type_text / select_dropdown):\n"
            f"{state.elements_prompt()}"
            f"{a11y_section}\n\n"
            "PAGE TEXT PREVIEW:\n"
            f"{state.text[:1500]}"
        )
        return [
            {"type": "text", "text": summary},
            {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": screenshot}},
        ]

    async def _t_read_a11y(self) -> str:
        result = await self.browser.accessibility_snapshot()
        return (
            "ACCESSIBILITY TREE (role + accessible name for each element):\n"
            "Use role/name values with click_by_role_name for reliable targeting.\n\n"
            + result
        )

    async def _capture_and_rank(self, query: str, action: str = "click", max_results: int = 8) -> list[dict]:
        state, ranked = await self.analyzer.capture_and_rank_candidates(
            query=query,
            action=action,
            max_results=max_results,
            task_context=self._current_task,
        )
        self.browser.store_elements(state.elements)
        return ranked

    def _decision_source(self) -> str:
        return "HYBRID" if getattr(self.provider, "_last_vision_injected", False) else "DOM"

    async def _t_get_action_candidates(self, query: str, action: str = "click", max_results: int = 8) -> str:
        ranked = await self._capture_and_rank(query=query, action=action, max_results=max_results)
        lines = [
            f"Action candidates for {action!r} and query {query!r}:",
            f"Decision source: {self._decision_source()}",
        ]
        if not ranked:
            return "\n".join(lines + ["No candidates found."])
        for idx, item in enumerate(ranked, 1):
            e = item["element"]
            label = e.get("label") or e.get("text") or e.get("placeholder") or e.get("name") or "(no label)"
            ctx = e.get("context") or e.get("heading") or ""
            lines.append(
                f'{idx}. [id={e.get("id")}] confidence={item["confidence"]} score={item["score"]} '
                f'source={item["source"]} tag={e.get("tag")} text="{str(label)[:80]}"'
                + (f' context="{str(ctx)[:80]}"' if ctx else "")
                + f" reason={item['reason']}"
            )
        return "\n".join(lines)

    async def _t_query_dom(self, query: str, max_results: int = 15) -> str:
        ranked = await self._capture_and_rank(query, action="click", max_results=max_results)
        top = [item for item in ranked if item["score"] > 0][: max(1, min(max_results, 30))]
        if not top:
            top = ranked[: max(1, min(max_results, 10))]
        lines = [f"Query: {query}", "Top matches (use IDs):"]
        for i, item in enumerate(top, 1):
            e = item["element"]
            label = e.get("label") or e.get("text") or e.get("placeholder") or e.get("name") or "(no label)"
            ctx = e.get("context", "")
            ctx_str = f' [context: {ctx[:60]}]' if ctx else ""
            lines.append(
                f'{i}. [id={e.get("id")}] score={item["score"]} confidence={item["confidence"]} '
                f'tag={e.get("tag")} type={e.get("type","")} '
                f'inView={bool(e.get("inView"))} text="{str(label)[:80]}"{ctx_str}'
            )
        return "\n".join(lines)

    async def _t_click_best_match(self, query: str) -> str:
        ranked = await self._capture_and_rank(query=query, action="click", max_results=8)
        best = ranked[0] if ranked else None
        if not best or best["score"] <= 0:
            return (
                f'ERROR: no matching element found for query "{query}". '
                f'Try read_accessibility_tree + click_by_role_name as an alternative.'
            )
        if best["confidence"] == "low":
            # Auto-retry: fetch a11y tree immediately so the agent can act without an extra round-trip
            a11y = await self.browser.accessibility_snapshot()
            a11y_snippet = a11y[:1400]
            return (
                f'AMBIGUOUS: Low confidence for click target "{query}". '
                f'DOM heuristics are uncertain here.\n\n'
                f'ACCESSIBILITY TREE (use click_by_role_name with role/name below):\n'
                f'{a11y_snippet}'
                + ('\n[...call read_accessibility_tree for full tree]' if len(a11y) > 1400 else '')
            )
        e = best["element"]
        elem_id = int(e["id"])
        sel = self.browser.get_selector(elem_id)
        if not sel:
            return f'ERROR: best-match id={e.get("id")} has no selector mapping. Call get_page_state first.'
        result = await self.browser.click(sel)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [DECISION] source={self._decision_source()} confidence={best['confidence']} reason={best['reason']}")
            self.console.print(f"  [SCREENSHOT] click_best_match -> {shot}")
            elem_desc = e.get("label") or e.get("text") or e.get("placeholder") or str(e.get("id"))
            return (
                f'{result}\nMatched "{query}" to id={elem_id} ({e.get("tag")}, "{elem_desc}")\n'
                f'Source: {self._decision_source()} | Confidence: {best["confidence"]} | Reason: {best["reason"]}\n'
                f"Checkpoint screenshot: {shot}\n"
                f"NEXT STEP: verify the expected change with get_page_state before proceeding."
            )
        return result

    async def _t_click_by_role_name(self, role: str, name: str, exact: bool = False) -> str:
        result = await self.browser.click_by_role_name(role, name, exact)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] click_by_role_name [{role}={name!r}] -> {shot}")
            return (
                f"{result}\n"
                f"Checkpoint screenshot: {shot}\n"
                f"NEXT STEP: verify the expected change with get_page_state before proceeding."
            )
        return result

    async def _t_type_by_role_name(self, role: str, name: str, text: str, exact: bool = False) -> str:
        result = await self.browser.fill_by_role_name(role, name, text, exact)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] type_by_role_name [{role}={name!r}] -> {shot}")
            return (
                f"{result}\n"
                f"Checkpoint screenshot: {shot}\n"
                f"NEXT STEP: verify input value with get_page_state before proceeding."
            )
        return result

    async def _t_type_best_match(self, query: str, text: str) -> str:
        ranked = await self._capture_and_rank(query=query, action="type", max_results=8)
        best = ranked[0] if ranked else None
        if not best or best["score"] <= 0:
            return f'ERROR: no matching text input found for query "{query}"'
        if best["confidence"] == "low":
            a11y = await self.browser.accessibility_snapshot()
            a11y_snippet = a11y[:1400]
            return (
                f'AMBIGUOUS: Low confidence for text input "{query}". '
                f'DOM heuristics are uncertain here.\n\n'
                f'ACCESSIBILITY TREE (use type_by_role_name with role/name below):\n'
                f'{a11y_snippet}'
                + ('\n[...call read_accessibility_tree for full tree]' if len(a11y) > 1400 else '')
            )
        e = best["element"]
        elem_id = int(e["id"])
        sel = self.browser.get_selector(elem_id)
        if not sel:
            return f'ERROR: best-match id={e.get("id")} has no selector mapping'
        result = await self.browser.fill(sel, text)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [DECISION] source={self._decision_source()} confidence={best['confidence']} reason={best['reason']}")
            self.console.print(f"  [SCREENSHOT] type_best_match -> {shot}")
            return (
                f'{result}\nMatched "{query}" to input id={elem_id}\n'
                f'Source: {self._decision_source()} | Confidence: {best["confidence"]} | Reason: {best["reason"]}\n'
                f"Checkpoint screenshot: {shot}"
            )
        return result

    async def _t_click(self, element_id: int) -> str:
        sel = self.browser.get_selector(element_id)
        if not sel:
            return f"ERROR: element {element_id} not found. Call get_page_state first."
        result = await self.browser.click(sel)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] click [{element_id}] -> {shot}")
            return f"{result}\nCheckpoint screenshot: {shot}"
        return result

    async def _t_hover(self, element_id: int) -> str:
        sel = self.browser.get_selector(element_id)
        if not sel:
            return f"ERROR: element {element_id} not found. Call get_page_state first."
        result = await self.browser.hover(sel)
        if result.startswith("OK"):
            await asyncio.sleep(0.3)
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] hover [{element_id}] -> {shot}")
            return f"{result}\nCheckpoint screenshot: {shot}"
        return result

    async def _t_type(self, element_id: int, text: str) -> str:
        sel = self.browser.get_selector(element_id)
        if not sel:
            return f"ERROR: element {element_id} not found. Call get_page_state first."
        result = await self.browser.fill(sel, text)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] type [{element_id}] -> {shot}")
            return f"{result}\nCheckpoint screenshot: {shot}"
        return result

    async def _t_select(self, element_id: int, value: str) -> str:
        sel = self.browser.get_selector(element_id)
        if not sel:
            return f"ERROR: element {element_id} not found. Call get_page_state first."
        result = await self.browser.select_option(sel, value)
        if result.startswith("OK"):
            shot = await self.browser.save_screenshot(False)
            self.console.print(f"  [SCREENSHOT] select [{element_id}] -> {shot}")
            return f"{result}\nCheckpoint screenshot: {shot}"
        return result

    async def _t_ask_user(self, question: str) -> str:
        self.console.print(f"\nAgent asks: {question}")
        ans = (
            await self._user_input_cb(question)
            if self._user_input_cb
            else await asyncio.to_thread(input, "Your answer: ")
        )
        return f"User replied: {ans}"

    async def _t_confirm(self, description: str) -> str:
        self.console.print(f"SECURITY CHECK: {description}")
        approved = (
            await self._confirm_cb(description)
            if self._confirm_cb
            else (await asyncio.to_thread(input, "Allow this action? (yes/no): ")).strip().lower()
            in ("yes", "y", "да", "д", "1")
        )
        return "APPROVED - proceed." if approved else "DENIED - stop and ask user what to do next."

    async def _t_delegate(self, task: str, context: str, role: str = "") -> str:
        """
        Delegate a sub-task to a specialized agent.
        Role is auto-detected from the task description if not provided.
        Returns a JSON string so the orchestrator can machine-process the outcome.
        """
        detected_role = role if role in _ROLE_PROMPTS else _detect_role(task)
        self.console.print(f"  [DELEGATE → {detected_role}] {task[:160]}")

        sub_provider = _clone_provider(self.provider)
        sub = AIAgent(
            browser=self.browser,
            analyzer=self.analyzer,
            provider=sub_provider,
            console=self.console,
            role=detected_role,
            max_turns=25,
        )
        if self._user_input_cb:
            sub.set_callbacks(self._user_input_cb, self._confirm_cb)

        full_task = task if not context.strip() else f"{task}\n\nContext:\n{context}"
        result = await sub.run_task(full_task)

        timed_out = result.startswith("(task did not complete")
        payload = {
            "role": detected_role,
            "status": "timeout" if timed_out else "completed",
            "summary": result,
        }
        return json.dumps(payload, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Provider cloning helper
# ---------------------------------------------------------------------------

def _clone_provider(p: AnyProvider) -> AnyProvider:
    if isinstance(p, AnthropicProvider):
        return AnthropicProvider(api_key=p.api_key, model=p.model)
    if isinstance(p, OpenAICompatProvider):
        return OpenAICompatProvider(api_key=p.api_key, model=p.model, base_url=p.base_url)
    raise TypeError(f"Cannot clone provider type: {type(p)}")
