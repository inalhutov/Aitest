import asyncio
import base64
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from playwright.async_api import async_playwright

PROFILE_DIR = Path.home() / ".ai_browser_agent" / "chrome_profile"
SCREENSHOT_DIR = Path.home() / ".ai_browser_agent" / "screenshots"
_MAX_SCREENSHOTS = 50


def _prune_screenshots() -> None:
    """Keep only the N most recent screenshots; delete the rest."""
    shots = sorted(SCREENSHOT_DIR.glob("screenshot_*.png"), key=lambda p: p.stat().st_mtime)
    for old in shots[:-_MAX_SCREENSHOTS]:
        try:
            old.unlink()
        except OSError:
            pass

_A11Y_SKIP_ROLES = frozenset({"none", "presentation", "generic", "LineBreak", "text"})
_A11Y_STRUCTURAL_ROLES = frozenset({
    "WebArea", "group", "list", "table", "row", "rowgroup", "listitem",
    "section", "navigation", "main", "banner", "contentinfo", "region",
})


def _a11y_collect(
    node: dict,
    depth: int,
    lines: list[str],
    max_depth: int = 8,
    max_lines: int = 500,
) -> None:
    if not node or depth > max_depth or len(lines) >= max_lines:
        return
    role = node.get("role", "")
    name = (node.get("name") or "").strip()
    value = node.get("value")
    description = (node.get("description") or "").strip()

    is_skip = role in _A11Y_SKIP_ROLES
    is_structural = role in _A11Y_STRUCTURAL_ROLES

    if not is_skip:
        indent = "  " * depth
        parts = [f"{indent}[{role}]"]
        if name:
            parts.append(f'"{name}"')
        if value is not None and str(value).strip() and str(value) != name:
            parts.append(f"= {str(value)[:60]!r}")
        if description and description != name:
            parts.append(f"({description[:80]})")
        flags: list[str] = []
        if node.get("disabled"):
            flags.append("disabled")
        if node.get("checked") is True:
            flags.append("checked")
        elif node.get("checked") is False and role in ("checkbox", "radio", "switch"):
            flags.append("unchecked")
        exp = node.get("expanded")
        if exp is True:
            flags.append("expanded")
        elif exp is False:
            flags.append("collapsed")
        if node.get("required"):
            flags.append("required")
        if flags:
            parts.append(f"[{', '.join(flags)}]")
        if not is_structural:
            lines.append(" ".join(parts))

    child_depth = depth if is_skip else depth + 1
    for child in node.get("children") or []:
        _a11y_collect(child, child_depth, lines, max_depth, max_lines)


class BrowserController:
    def __init__(self) -> None:
        self._playwright = None
        self._browser: Optional[Any] = None
        self._context: Optional[Any] = None
        self._page: Optional[Any] = None
        self._owns_context = True
        self._attached_mode = False
        self._element_map: dict[int, dict] = {}

    async def start(
        self,
        headless: bool = False,
        browser_mode: str = "persistent",
        cdp_url: str = "http://127.0.0.1:9222",
    ) -> None:
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        self._playwright = await async_playwright().start()

        mode = (browser_mode or "persistent").strip().lower()
        if mode == "attach":
            self._attached_mode = True
            self._owns_context = False
            try:
                self._browser = await self._playwright.chromium.connect_over_cdp(cdp_url)
            except Exception as e:
                raise RuntimeError(
                    "Could not connect to existing Chrome via CDP. "
                    "Start Chrome with remote debugging enabled first.\n"
                    "PowerShell:\n"
                    "& \"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe\" "
                    "--remote-debugging-port=9222"
                ) from e
            self._context = (
                self._browser.contexts[0]
                if self._browser.contexts
                else await self._browser.new_context(viewport={"width": 1280, "height": 800})
            )
        else:
            self._attached_mode = False
            self._owns_context = True
            self._context = await self._playwright.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=headless,
                viewport={"width": 1280, "height": 800},
                args=["--disable-blink-features=AutomationControlled", "--start-maximized"],
                ignore_https_errors=True,
            )

        self._page = self._context.pages[-1] if self._context.pages else await self._context.new_page()
        await self._page.bring_to_front()

    async def stop(self) -> None:
        if self._context and self._owns_context:
            await self._context.close()
        if self._playwright:
            await self._playwright.stop()

    @property
    def attached_mode(self) -> bool:
        return self._attached_mode

    @property
    def page(self) -> Any:
        return self._page

    def store_elements(self, elements: list[dict]) -> None:
        self._element_map = {e["id"]: e for e in elements}

    def get_selector(self, element_id: int) -> Optional[str]:
        if element_id in self._element_map:
            return f'[data-agentid="{element_id}"]'
        return None

    async def screenshot_b64(self) -> str:
        data = await self._page.screenshot(type="jpeg", quality=80)
        return base64.b64encode(data).decode()

    async def save_screenshot(self, full_page: bool = False) -> str:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        out = SCREENSHOT_DIR / f"screenshot_{ts}.png"
        await self._page.screenshot(path=str(out), full_page=full_page)
        _prune_screenshots()
        return str(out)

    async def navigate(self, url: str) -> str:
        try:
            if not url.startswith(("http://", "https://", "file://")):
                url = "https://" + url
            await self._page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            await asyncio.sleep(1.2)
            return f"OK: navigated to {self._page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def go_back(self) -> str:
        try:
            await self._page.go_back(wait_until="domcontentloaded", timeout=15_000)
            await asyncio.sleep(0.8)
            return "OK: went back"
        except Exception as e:
            return f"ERROR: {e}"

    async def click(self, selector: str) -> str:
        try:
            await self._page.click(selector, timeout=10_000)
            await asyncio.sleep(1.0)
            return f"OK: clicked ({selector}). Current URL: {self._page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def hover(self, selector: str) -> str:
        try:
            await self._page.hover(selector, timeout=8_000)
            await asyncio.sleep(0.5)
            return f"OK: hovered ({selector}). Current URL: {self._page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def click_by_role_name(self, role: str, name: str, exact: bool = False) -> str:
        """Click using ARIA role + accessible name - more reliable than raw DOM selectors."""
        try:
            async def choose_best(locator: Any) -> Optional[Any]:
                count = await locator.count()
                if count == 0:
                    return None
                # Prefer visible + enabled candidates.
                for i in range(min(count, 12)):
                    cand = locator.nth(i)
                    try:
                        if await cand.is_visible() and await cand.is_enabled():
                            return cand
                    except Exception:
                        continue
                # Fallback to any visible candidate.
                for i in range(min(count, 12)):
                    cand = locator.nth(i)
                    try:
                        if await cand.is_visible():
                            return cand
                    except Exception:
                        continue
                return locator.first

            # Generic modal-first targeting:
            # If a visible dialog/alertdialog exists, prioritize targets within it.
            dialogs = self._page.locator('[role="dialog"], [role="alertdialog"], dialog, [aria-modal="true"]')
            dialog_count = await dialogs.count()
            target = None
            for i in range(min(dialog_count, 6)):
                dlg = dialogs.nth(i)
                try:
                    if not await dlg.is_visible():
                        continue
                except Exception:
                    continue
                scoped = dlg.get_by_role(role, name=name, exact=exact)  # type: ignore[arg-type]
                target = await choose_best(scoped)
                if target is not None:
                    break

            # Fallback to global search if no dialog-scoped target found.
            if target is None:
                loc = self._page.get_by_role(role, name=name, exact=exact)  # type: ignore[arg-type]
                target = await choose_best(loc)
            if target is None:
                return f"ERROR: no element with role={role!r} name={name!r} found on page"
            await target.click(timeout=10_000)
            await asyncio.sleep(1.0)
            return f"OK: clicked role={role!r} name={name!r}. URL: {self._page.url}"
        except Exception as e:
            return f"ERROR: {e}"

    async def fill_by_role_name(self, role: str, name: str, text: str, exact: bool = False) -> str:
        """Type into an input/textbox located by ARIA role + accessible name."""
        try:
            loc = self._page.get_by_role(role, name=name, exact=exact)  # type: ignore[arg-type]
            count = await loc.count()
            if count == 0:
                # Fallback: try get_by_label which covers <label for="..."> associations
                loc = self._page.get_by_label(name, exact=exact)
                count = await loc.count()
                if count == 0:
                    return f"ERROR: no input with role={role!r} name={name!r} found on page"
            target = loc.first
            if count > 1:
                for i in range(min(count, 12)):
                    cand = loc.nth(i)
                    try:
                        if await cand.is_visible() and await cand.is_enabled():
                            target = cand
                            break
                    except Exception:
                        continue
            await target.fill(text, timeout=8_000)
            return f"OK: typed into role={role!r} name={name!r}"
        except Exception as e:
            return f"ERROR: {e}"

    async def fill(self, selector: str, text: str) -> str:
        try:
            await self._page.fill(selector, text, timeout=8_000)
            return "OK: text entered"
        except Exception as e:
            return f"ERROR: {e}"

    async def select_option(self, selector: str, value: str) -> str:
        try:
            await self._page.select_option(selector, value=value, timeout=8_000)
            return "OK: option selected"
        except Exception:
            try:
                await self._page.select_option(selector, label=value, timeout=8_000)
                return "OK: option selected by label"
            except Exception as e2:
                return f"ERROR: {e2}"

    async def press_key(self, key: str) -> str:
        try:
            await self._page.keyboard.press(key)
            await asyncio.sleep(0.4)
            return f"OK: pressed {key}"
        except Exception as e:
            return f"ERROR: {e}"

    async def scroll(self, direction: str, pixels: int = 600) -> str:
        dy = pixels if direction.lower() == "down" else -pixels
        await self._page.evaluate(f"window.scrollBy(0, {dy})")
        await asyncio.sleep(0.3)
        return f"OK: scrolled {direction} {pixels}px"

    async def wait(self, seconds: float) -> str:
        secs = max(0.5, min(float(seconds), 30.0))
        await asyncio.sleep(secs)
        return f"OK: waited {secs:.1f}s"

    async def accessibility_snapshot(self) -> str:
        """Return the semantic accessibility tree — more reliable than raw DOM for element identification."""
        try:
            snap = await self._page.accessibility.snapshot(interesting_only=True)
            if not snap:
                return "(empty accessibility tree — page may not expose ARIA)"
            lines: list[str] = []
            _a11y_collect(snap, 0, lines)
            text = "\n".join(lines)
            return text if text.strip() else "(no accessible nodes found)"
        except Exception as e:
            return f"ERROR getting accessibility tree: {e}"
