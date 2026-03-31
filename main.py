import asyncio
import os
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

def _load_env_file() -> None:
    env_path = Path(".env")
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()


class SimpleConsole:
    def print(self, text: str = "") -> None:
        print(text)

    def input(self, prompt: str) -> str:
        return input(prompt)


console = SimpleConsole()


async def _ask_user(_: str) -> str:
    return await asyncio.to_thread(input, "  Your answer: ")


async def _confirm_action(_: str) -> bool:
    raw = await asyncio.to_thread(input, "  Allow? (yes / no): ")
    return raw.strip().lower() in ("yes", "y", "да", "д", "1")


def _build_provider():
    from agent.providers import build_provider

    provider = os.getenv("PROVIDER", "openai").strip().lower()
    if provider == "openai":
        key = os.getenv("OPENAI_API_KEY", "").strip()
        model = os.getenv("OPENAI_MODEL", "gpt-5").strip()
        base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
        if not key:
            console.print("ERROR: OPENAI_API_KEY not set.")
            sys.exit(1)
        console.print(f"Provider: OpenAI-compatible ({model})")
        return build_provider("openai", api_key=key, model=model, base_url=base)
    if provider == "anthropic":
        key = os.getenv("ANTHROPIC_API_KEY", "").strip()
        model = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022").strip()
        if not key:
            console.print("ERROR: ANTHROPIC_API_KEY not set.")
            sys.exit(1)
        console.print(f"Provider: Anthropic ({model})")
        return build_provider("anthropic", api_key=key, model=model)
    console.print("ERROR: Unknown PROVIDER. Use openai or anthropic.")
    sys.exit(1)


async def main() -> None:
    from agent.agent import AIAgent
    from agent.browser import BrowserController
    from agent.page import PageAnalyzer

    console.print("\nAI Browser Agent - autonomous web automation\n")

    provider = _build_provider()
    browser_mode = os.getenv("BROWSER_MODE", "persistent").strip().lower()
    cdp_url = os.getenv("CHROME_CDP_URL", "http://127.0.0.1:9222").strip()
    attach_fallback = os.getenv("ATTACH_FALLBACK_TO_PERSISTENT", "1").strip().lower() in ("1", "true", "yes", "y")

    console.print("Launching browser...")
    browser = BrowserController()
    try:
        await browser.start(browser_mode=browser_mode, cdp_url=cdp_url)
    except RuntimeError as e:
        if browser_mode == "attach" and attach_fallback:
            console.print(f"Browser attach failed: {e}\nAttach failed, switching to persistent mode.")
            await browser.start(browser_mode="persistent")
        else:
            console.print(f"Browser attach failed: {e}")
            sys.exit(1)
    console.print("Browser ready.")
    console.print("How to use:\n  - Type any task and press Enter\n  - Watch browser actions\n  - Type quit to exit")

    analyzer = PageAnalyzer(browser.page)
    agent = AIAgent(browser=browser, analyzer=analyzer, provider=provider, console=console)
    agent.set_callbacks(user_input=_ask_user, confirm=_confirm_action)

    try:
        while True:
            console.print()
            console.print("-" * 60)
            task = (await asyncio.to_thread(lambda: console.input("Task > "))).strip()
            if not task:
                continue
            if task.lower() in ("quit", "exit", "q", "выход"):
                break
            await agent.run_task(task)
    except KeyboardInterrupt:
        console.print("\nInterrupted.")
    finally:
        console.print("Detaching from browser..." if browser.attached_mode else "Closing browser...")
        await browser.stop()
        console.print("Goodbye!")


if __name__ == "__main__":
    asyncio.run(main())
