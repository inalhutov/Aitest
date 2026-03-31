TOOL_DEFINITIONS: list[dict] = [
    {
        "name": "get_page_state",
        "description": (
            "Capture current page state: URL, title, interactive DOM elements with numeric IDs, "
            "ACCESSIBILITY TREE (role/name pairs for click_by_role_name / type_by_role_name), "
            "page text preview, and a screenshot — all in one call. "
            "Always call this first. DOM IDs → click_element/type_text. A11y names → click_by_role_name/type_by_role_name."
        ),
        "input_schema": {"type": "object", "properties": {"reason": {"type": "string"}}, "required": []},
    },
    {
        "name": "read_accessibility_tree",
        "description": (
            "Read the semantic accessibility (ARIA) tree of the current page. "
            "Returns structured role/name pairs for all meaningful elements. "
            "More reliable than raw DOM when buttons are icon-only, labels are ambiguous, "
            "or the page uses complex widgets (tabs, dialogs, carousels, menus). "
            "Use the role/name output to call click_by_role_name."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "describe_visible_region",
        "description": "Compact description of what is currently visible on screen.",
        "input_schema": {
            "type": "object",
            "properties": {"max_chars": {"type": "integer", "default": 900}},
            "required": [],
        },
    },
    {
        "name": "extract_page_text",
        "description": "Extract full visible page text when you need to read content, prices, statuses, or other data.",
        "input_schema": {
            "type": "object",
            "properties": {"max_chars": {"type": "integer", "default": 5000}},
            "required": [],
        },
    },
    {
        "name": "query_dom",
        "description": "Search DOM semantically for a target element and return best matches with IDs.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer", "default": 15},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_action_candidates",
        "description": "Rank the best clickable or typable element candidates for a query, with confidence scores and reasons.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "action": {"type": "string", "enum": ["click", "type"], "default": "click"},
                "max_results": {"type": "integer", "default": 8},
            },
            "required": ["query"],
        },
    },
    {
        "name": "click_best_match",
        "description": "Find the best matching interactive element by semantic query and click it.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "click_by_role_name",
        "description": (
            "Click an element using its ARIA role + accessible name. "
            "More reliable than DOM IDs on accessible pages. "
            "Use after read_accessibility_tree to get exact role/name values. "
            "Examples: role='button' name='Submit', role='link' name='Home', role='tab' name='Reviews'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "ARIA role: button, link, checkbox, textbox, combobox, menuitem, tab, option, switch, etc.",
                },
                "name": {
                    "type": "string",
                    "description": "Accessible name — the aria-label value or visible text label of the element.",
                },
                "exact": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, name must match exactly (case-sensitive). Default is substring/case-insensitive.",
                },
            },
            "required": ["role", "name"],
        },
    },
    {
        "name": "type_into_best_match",
        "description": "Find best matching text input by semantic query and type text into it.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}, "text": {"type": "string"}},
            "required": ["query", "text"],
        },
    },
    {
        "name": "type_by_role_name",
        "description": (
            "Type text into a text input or textarea located by its ARIA role + accessible name. "
            "More reliable than DOM IDs for accessible pages. "
            "Use after reading the accessibility tree from get_page_state. "
            "Examples: role='textbox' name='Email', role='searchbox' name='Search', role='spinbutton' name='Quantity'. "
            "Falls back to get_by_label if the role locator finds nothing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role": {
                    "type": "string",
                    "description": "ARIA role of the input: textbox, searchbox, spinbutton, combobox, etc.",
                },
                "name": {
                    "type": "string",
                    "description": "Accessible name — the aria-label value or visible text label of the input.",
                },
                "text": {
                    "type": "string",
                    "description": "Text to type into the input field.",
                },
                "exact": {
                    "type": "boolean",
                    "default": False,
                    "description": "If true, name must match exactly. Default is substring/case-insensitive.",
                },
            },
            "required": ["role", "name", "text"],
        },
    },
    {
        "name": "hover_element",
        "description": (
            "Hover over an element by ID to reveal tooltips, dropdown menus, or hover-triggered UI. "
            "Use before clicking when a menu requires hovering to appear."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"element_id": {"type": "integer"}},
            "required": ["element_id"],
        },
    },
    {
        "name": "take_screenshot",
        "description": "Save a screenshot to disk for debugging or visual verification.",
        "input_schema": {
            "type": "object",
            "properties": {"full_page": {"type": "boolean", "default": False}},
            "required": [],
        },
    },
    {
        "name": "navigate_to_url",
        "description": "Navigate browser to a URL.",
        "input_schema": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
    },
    {
        "name": "click_element",
        "description": "Click element by ID from get_page_state.",
        "input_schema": {"type": "object", "properties": {"element_id": {"type": "integer"}}, "required": ["element_id"]},
    },
    {
        "name": "type_text",
        "description": "Type text into input/textarea by element ID.",
        "input_schema": {
            "type": "object",
            "properties": {"element_id": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["element_id", "text"],
        },
    },
    {
        "name": "select_dropdown",
        "description": "Select option in a <select> dropdown by element ID.",
        "input_schema": {
            "type": "object",
            "properties": {"element_id": {"type": "integer"}, "value": {"type": "string"}},
            "required": ["element_id", "value"],
        },
    },
    {
        "name": "press_key",
        "description": "Press a keyboard key such as Enter, Tab, Escape, ArrowDown, etc.",
        "input_schema": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]},
    },
    {
        "name": "go_back",
        "description": "Go back in browser history.",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "scroll_page",
        "description": "Scroll page up or down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"]},
                "pixels": {"type": "integer", "default": 600},
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait_for_page",
        "description": "Wait for dynamic content, animations, or network requests to complete.",
        "input_schema": {"type": "object", "properties": {"seconds": {"type": "number", "default": 2}}, "required": []},
    },
    {
        "name": "ask_user",
        "description": "Ask the user for information that is genuinely missing and cannot be inferred.",
        "input_schema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
    },
    {
        "name": "confirm_destructive_action",
        "description": "MANDATORY before irreversible actions: purchases, deletions, submissions with financial consequences.",
        "input_schema": {
            "type": "object",
            "properties": {"action_description": {"type": "string"}},
            "required": ["action_description"],
        },
    },
    {
        "name": "delegate_subtask",
        "description": (
            "Delegate a focused sub-task to a specialized sub-agent with its own context window and tool set. "
            "The sub-agent runs and returns a result summary. Use for: reading/extraction, form interaction, "
            "verification, or navigation that are separable from the current step. "
            "Specify role: 'navigator' | 'extractor' | 'interactor' | 'verifier'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The focused task for the sub-agent to complete."},
                "context": {"type": "string", "description": "Relevant context from the current page or prior steps."},
                "role": {
                    "type": "string",
                    "enum": ["navigator", "extractor", "interactor", "verifier"],
                    "description": (
                        "navigator=find pages/links/search; "
                        "extractor=read/extract data (read-only); "
                        "interactor=fill forms/click complex elements; "
                        "verifier=confirm expected state (read-only)"
                    ),
                },
            },
            "required": ["task", "context", "role"],
        },
    },
    {
        "name": "task_complete",
        "description": "Signal task completion with a clear summary of what was accomplished.",
        "input_schema": {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]},
    },
]

# Tool sets per sub-agent role — each role gets only the tools it needs
_ROLE_TOOL_NAMES: dict[str, list[str]] = {
    "navigator": [
        "get_page_state", "read_accessibility_tree", "navigate_to_url", "click_best_match",
        "click_by_role_name", "query_dom", "get_action_candidates", "scroll_page",
        "wait_for_page", "go_back", "take_screenshot", "ask_user", "task_complete",
    ],
    "extractor": [
        "get_page_state", "read_accessibility_tree", "extract_page_text",
        "describe_visible_region", "scroll_page", "wait_for_page", "take_screenshot", "task_complete",
    ],
    "interactor": [
        "get_page_state", "read_accessibility_tree", "click_best_match", "type_into_best_match",
        "click_element", "type_text", "select_dropdown", "click_by_role_name", "type_by_role_name",
        "hover_element", "press_key", "wait_for_page", "take_screenshot", "scroll_page",
        "query_dom", "get_action_candidates", "ask_user", "task_complete",
    ],
    "verifier": [
        "get_page_state", "read_accessibility_tree", "extract_page_text",
        "describe_visible_region", "take_screenshot", "scroll_page", "task_complete",
    ],
}


def tools_for_role(role: str) -> list[dict]:
    """Return the tool definitions allowed for a given sub-agent role."""
    name_index = {t["name"]: t for t in TOOL_DEFINITIONS}
    names = _ROLE_TOOL_NAMES.get(role, [t["name"] for t in TOOL_DEFINITIONS if t["name"] != "delegate_subtask"])
    return [name_index[n] for n in names if n in name_index]


SUBAGENT_TOOL_DEFINITIONS = [t for t in TOOL_DEFINITIONS if t["name"] != "delegate_subtask"]
