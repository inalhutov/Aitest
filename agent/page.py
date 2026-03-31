"""
Page analysis: DOM extraction, element ranking, and page state capture.

Design principles:
- Scoring is purely query-driven — no domain-specific hardcoding.
- A11y-quality bonus: elements with explicit aria-label are more reliable targets.
- task_context enriches scoring generically (extra terms from task description),
  without any hardcoded domain assumptions (e-commerce, banking, etc.).
- Icon-only controls (very short label/text) are supported via context proximity scoring.
"""
import re

from playwright.async_api import Page

_JS_INJECT_AND_EXTRACT = r"""
() => {
    document.querySelectorAll('[data-agentid]').forEach(el => el.removeAttribute('data-agentid'));
    const selectors = [
        'a[href]','button','input:not([type="hidden"])','select','textarea',
        '[role="button"]','[role="link"]','[role="checkbox"]','[role="radio"]',
        '[role="tab"]','[role="menuitem"]','[role="option"]','[role="combobox"]',
        '[role="switch"]','[onclick]','[tabindex]:not([tabindex="-1"])'
    ];

    const clean = (t) => (t || '').replace(/\s+/g, ' ').trim();
    const hasLetters = (t) => /\p{L}/u.test(t || '');
    const looksUseful = (t) => {
        const s = clean(t);
        return s.length >= 2 && s.length <= 180 && hasLetters(s);
    };

    // Semantic-first container detection — no pixel/size heuristics.
    // Priority: explicit semantic HTML tags > ARIA widget roles > heading presence > text length.
    const SEMANTIC_TAGS = new Set([
        'article','li','section','form','dialog','fieldset','details','td','th','tr','option'
    ]);
    const SEMANTIC_ROLES = new Set([
        'listitem','row','gridcell','treeitem','option','tab','menuitem','article','group'
    ]);

    const findContainer = (el) => {
        // Pass 1: walk up and find the nearest semantic HTML tag or ARIA role (most reliable, layout-independent)
        let p = el.parentElement;
        for (let i = 0; i < 7 && p && p !== document.body; i++, p = p.parentElement) {
            const tag = (p.tagName || '').toLowerCase();
            const role = p.getAttribute('role') || '';
            if (SEMANTIC_TAGS.has(tag) || SEMANTIC_ROLES.has(role)) return p;
        }
        // Pass 2: nearest ancestor that directly contains a heading element
        p = el.parentElement;
        for (let i = 0; i < 5 && p && p !== document.body; i++, p = p.parentElement) {
            const h = p.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
            if (h && h !== el && !el.contains(h)) return p;
        }
        // Pass 3: nearest ancestor with a reasonable text length (simple fallback, no size checks)
        p = el.parentElement;
        for (let i = 0; i < 4 && p && p !== document.body; i++, p = p.parentElement) {
            const text = clean(p.innerText || p.textContent || '');
            if (text.length >= 5 && text.length <= 400) return p;
        }
        return el.parentElement || el;
    };

    const extractContext = (el) => {
        const container = findContainer(el);
        const containerText = clean(container?.innerText || container?.textContent || '').slice(0, 220);

        // Prefer explicit heading over generic text children
        let heading = '';
        try {
            const headingEl = container.querySelector('h1,h2,h3,h4,h5,h6,[role="heading"]');
            if (headingEl && headingEl !== el && !headingEl.contains(el) && !el.contains(headingEl)) {
                heading = clean(headingEl.innerText || headingEl.textContent || '').slice(0, 100);
            }
        } catch (_) {}

        // Collect direct text-bearing children for context fallback
        let bestCandidate = '';
        let bestScore = -1;
        try {
            for (const child of container.children || []) {
                if (child === el || child.contains(el) || el.contains(child)) continue;
                const t = clean(child.innerText || child.textContent || '');
                if (!looksUseful(t)) continue;
                // Score: letters present, reasonable length, multiple words = better
                let s = 0;
                if (hasLetters(t)) s += 5;
                if (t.length >= 6 && t.length <= 90) s += 3;
                if (t.split(' ').length >= 2) s += 2;
                if (/^\d/.test(t)) s -= 2;
                if (s > bestScore) { bestScore = s; bestCandidate = t; }
            }
        } catch (_) {}

        const context = (looksUseful(heading) ? heading : bestCandidate).slice(0, 100);
        const priceMatch = containerText.match(/(\d[\d\s]{0,12})\s*[₽$€£¥]/);
        return {
            context,
            heading,
            containerText,
            price: priceMatch ? priceMatch[0].trim() : ''
        };
    };

    const seen = new WeakSet();
    const all = [];
    for (const sel of selectors) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (!seen.has(el)) {
                    seen.add(el);
                    all.push(el);
                }
            });
        } catch (_) {}
    }

    let id = 1;
    const out = [];
    for (const el of all) {
        const r = el.getBoundingClientRect();
        if (r.width < 3 || r.height < 3) continue;
        const s = getComputedStyle(el);
        if (s.display === 'none' || s.visibility === 'hidden' || parseFloat(s.opacity) < 0.05) continue;

        const inView = r.bottom > -120 && r.top < innerHeight + 250 && r.right > -80 && r.left < innerWidth + 80;
        el.setAttribute('data-agentid', String(id));

        const text = clean(el.innerText || el.textContent || '').slice(0, 80);

        // Compute accessible name following the W3C priority order:
        // aria-label > aria-labelledby (resolved text) > title > alt
        let label = el.getAttribute('aria-label') || '';
        if (!label) {
            const lbId = el.getAttribute('aria-labelledby');
            if (lbId) {
                const lbEl = document.getElementById(lbId);
                label = lbEl ? clean(lbEl.innerText || lbEl.textContent || '') : '';
            }
        }
        if (!label) label = el.getAttribute('title') || el.getAttribute('alt') || '';
        label = clean(label).slice(0, 120);

        const ctx = extractContext(el);

        out.push({
            id: id++,
            tag: el.tagName.toLowerCase(),
            type: el.type || '',
            role: el.getAttribute('role') || '',
            text,
            label,
            context: ctx.context,
            heading: ctx.heading,
            containerText: ctx.containerText,
            price: ctx.price,
            placeholder: el.placeholder || '',
            name: el.name || '',
            value: el.value ? String(el.value).slice(0, 40) : '',
            href: (el.tagName === 'A' && el.href) ? el.href.slice(0, 120) : '',
            disabled: !!el.disabled,
            checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : null,
            inView,
            x: Math.round(r.left + r.width / 2),
            y: Math.round(r.top + r.height / 2),
            width: Math.round(r.width),
            height: Math.round(r.height),
        });
    }
    return out;
}
"""

_JS_GET_TEXT = r"""
() => {
    const clone = document.body.cloneNode(true);
    clone.querySelectorAll('script,style,noscript,svg,canvas,iframe,[aria-hidden="true"]').forEach(n => n.remove());
    const t = clone.innerText || '';
    return t.replace(/[ \t]+/g, ' ').replace(/\n{3,}/g, '\n\n').trim();
}
"""

_JS_QUERY_CANDIDATES = r"""
(maxItems) => {
    const selectors = [
        'button','a[href]','input:not([type="hidden"])','select','textarea',
        '[role="button"]','[role="link"]','[role="option"]',
        'h1,h2,h3,h4,[data-testid],[aria-label],label'
    ];
    const seen = new WeakSet();
    const out = [];

    const toSelector = (el) => {
        if (el.id) return `#${el.id}`;
        const c = (el.className || '').toString().trim().split(/\s+/).filter(Boolean).slice(0,2);
        if (c.length) return `${el.tagName.toLowerCase()}.${c.join('.')}`;
        return el.tagName.toLowerCase();
    };

    for (const sel of selectors) {
        try {
            document.querySelectorAll(sel).forEach(el => {
                if (seen.has(el)) return;
                seen.add(el);
                const r = el.getBoundingClientRect();
                if (r.width < 2 || r.height < 2) return;
                const st = getComputedStyle(el);
                if (st.display === 'none' || st.visibility === 'hidden') return;
                const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 180);
                const aria = (el.getAttribute('aria-label') || '').slice(0, 120);
                const placeholder = (el.getAttribute('placeholder') || '').slice(0, 120);
                const testid = (el.getAttribute('data-testid') || '').slice(0, 80);
                const href = (el.getAttribute('href') || '').slice(0, 120);
                out.push({
                    tag: el.tagName.toLowerCase(),
                    role: el.getAttribute('role') || '',
                    selector: toSelector(el),
                    text,
                    aria,
                    placeholder,
                    testid,
                    href,
                    inView: r.bottom > -80 && r.top < innerHeight + 150
                });
            });
        } catch (_) {}
    }
    return out.slice(0, maxItems || 400);
}
"""


class PageState:
    def __init__(self, url: str, title: str, elements: list[dict], text: str):
        self.url = url
        self.title = title
        self.elements = elements
        self.text = text

    def elements_prompt(self, max_in_view: int = 60, max_below: int = 20) -> str:
        in_view = [e for e in self.elements if e.get("inView")]
        below = [e for e in self.elements if not e.get("inView")]

        def fmt(e: dict) -> str:
            kind = e["tag"]
            if kind == "input":
                kind = e["type"] or "input"
            elif kind == "a":
                kind = "link"
            elif kind == "select":
                kind = "dropdown"
            elif kind == "textarea":
                kind = "textarea"
            elif e.get("role") == "button" or kind == "button":
                kind = "button"
            label = e["label"] or e["text"] or e["placeholder"] or e["name"]
            line = f'[{e["id"]}] {kind}'
            if label:
                line += f' "{label[:60]}"'
            ctx = (e.get("context") or "").strip()
            heading = (e.get("heading") or "").strip()
            price = (e.get("price") or "").strip()
            if heading:
                line += f' [heading: {heading[:60]}]'
            if ctx and ctx != heading:
                line += f' [context: {ctx[:70]}]'
            if price:
                line += f" [price: {price}]"
            extras = []
            if e.get("href"):
                extras.append("link")
            if e.get("value"):
                extras.append(f'value={e["value"][:20]}')
            if e.get("checked") is True:
                extras.append("checked")
            if e.get("disabled"):
                extras.append("disabled")
            if extras:
                line += " (" + ", ".join(extras) + ")"
            return line

        lines = [fmt(e) for e in in_view[:max_in_view]]
        if len(in_view) > max_in_view:
            lines.append(f"[... {len(in_view)-max_in_view} more in-view elements]")
        if below:
            lines.append("")
            lines.append(f"[Below fold: showing {min(max_below, len(below))} of {len(below)}]")
            lines.extend(fmt(e) for e in below[:max_below])
        return "\n".join(lines) if lines else "(no interactive elements found)"


class PageAnalyzer:
    def __init__(self, page: Page):
        self.page = page

    async def capture(self) -> PageState:
        try:
            title = await self.page.title()
        except Exception:
            title = ""
        try:
            elements: list[dict] = await self.page.evaluate(_JS_INJECT_AND_EXTRACT)
        except Exception:
            elements = []
        try:
            text = (await self.page.evaluate(_JS_GET_TEXT))[:5000]
        except Exception:
            text = ""
        return PageState(url=self.page.url, title=title, elements=elements, text=text)

    async def get_full_text(self, max_chars: int = 15_000) -> str:
        try:
            return (await self.page.evaluate(_JS_GET_TEXT))[:max_chars]
        except Exception as e:
            return f"ERROR extracting text: {e}"

    async def describe_visible_region(self, max_chars: int = 900) -> str:
        state = await self.capture()
        lines = [
            f"URL: {state.url}",
            f"Title: {state.title}",
            "Visible region summary:",
        ]
        visible = [e for e in state.elements if e.get("inView")]
        for e in visible[:12]:
            label = e.get("label") or e.get("text") or e.get("placeholder") or e.get("name") or "(no label)"
            bits = [f'[{e["id"]}] {e.get("tag")} "{str(label)[:50]}"']
            if e.get("context"):
                bits.append(f'context="{str(e["context"])[:60]}"')
            if e.get("heading"):
                bits.append(f'heading="{str(e["heading"])[:40]}"')
            lines.append(" | ".join(bits))
        text_preview = state.text[:max_chars]
        if text_preview:
            lines.append("")
            lines.append("Visible text preview:")
            lines.append(text_preview)
        return "\n".join(lines)

    async def capture_and_rank_candidates(
        self,
        query: str,
        action: str = "click",
        max_results: int = 10,
        task_context: str = "",
    ) -> tuple[PageState, list[dict]]:
        state = await self.capture()
        return state, _rank_candidates(
            state,
            query=query,
            action=action,
            max_results=max_results,
            task_context=task_context,
        )

    async def rank_action_candidates(
        self,
        query: str,
        action: str = "click",
        max_results: int = 10,
        task_context: str = "",
    ) -> list[dict]:
        state = await self.capture()
        return _rank_candidates(
            state,
            query=query,
            action=action,
            max_results=max_results,
            task_context=task_context,
        )

    async def query_dom(self, query: str, max_results: int = 15) -> str:
        try:
            candidates: list[dict] = await self.page.evaluate(_JS_QUERY_CANDIDATES, 500)
        except Exception as e:
            return f"ERROR querying DOM: {e}"

        ranked = _rank_generic_dom_candidates(candidates, query, max_results)
        lines = [f"DOM query: {query}", "Top matches:"]
        for i, item in enumerate(ranked, 1):
            c = item["candidate"]
            label = c.get("text") or c.get("aria") or c.get("placeholder") or "(no text)"
            bits = [f"{i}. score={item['score']} {c.get('tag','?')} {c.get('selector','')}", f'"{label[:120]}"']
            if c.get("href"):
                bits.append(f"href={c['href'][:80]}")
            if c.get("inView"):
                bits.append("in-view")
            lines.append(" | ".join(bits))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Ranking helpers — fully generic, no domain hardcoding
# ---------------------------------------------------------------------------

def _terms(text: str) -> list[str]:
    """Tokenize text into lowercase terms, filtering noise."""
    return [t for t in re.findall(r"[a-zа-яёA-ZА-ЯЁ0-9]+", text.lower()) if len(t) > 1]


def _confidence(score: int) -> str:
    if score >= 18:
        return "high"
    if score >= 11:
        return "medium"
    return "low"


def _rank_candidates(
    state: "PageState",
    query: str,
    action: str = "click",
    max_results: int = 10,
    task_context: str = "",
) -> list[dict]:
    """
    Score and rank interactive elements against a query.

    Scoring is intentionally domain-agnostic:
    - Primary signal: query term matches in label/context/metadata.
    - Secondary signal: extra terms extracted from task_context (generic enrichment).
    - Quality signals: aria-label presence, visibility, element type fit.
    - No hardcoded domain terms (e-commerce, banking, etc.).
    """
    q_terms = _terms(query)
    q_set = set(q_terms)
    # Generic task context enrichment: additional unique terms from the broader task
    extra_terms = [t for t in _terms(task_context) if t not in q_set and len(t) > 2][:10]

    ranked: list[dict] = []

    for e in state.elements:
        tag = str(e.get("tag") or "").lower()
        etype = str(e.get("type") or "").lower()
        role = str(e.get("role") or "").lower()
        label = str(e.get("label") or "")
        text = str(e.get("text") or "")
        placeholder = str(e.get("placeholder") or "")
        name = str(e.get("name") or "")
        href = str(e.get("href") or "")
        context = str(e.get("context") or "")
        heading = str(e.get("heading") or "")
        price = str(e.get("price") or "")
        container = str(e.get("containerText") or "")

        if e.get("disabled"):
            continue
        if action == "type" and tag not in ("input", "textarea"):
            continue

        haystacks = {
            "label": " ".join([label.lower(), text.lower()]).strip(),
            "context": " ".join([context.lower(), heading.lower(), container.lower()]).strip(),
            "meta": " ".join([placeholder.lower(), name.lower(), href.lower(), role, tag, etype, price.lower()]).strip(),
        }
        score = 0
        reasons: list[str] = []

        # --- Primary: query term matching ---
        for term in q_terms:
            if term in haystacks["label"]:
                score += 7
                reasons.append(f'label matches "{term}"')
            elif term in haystacks["context"]:
                score += 5
                reasons.append(f'context matches "{term}"')
            elif term in haystacks["meta"]:
                score += 3
                reasons.append(f'meta matches "{term}"')

        # --- Secondary: generic task context enrichment ---
        for term in extra_terms:
            if term in haystacks["label"]:
                score += 2
                reasons.append(f'task term in label "{term}"')
            elif term in haystacks["context"]:
                score += 1
                reasons.append(f'task term in context "{term}"')

        # --- Full-query bonus ---
        full_query = query.lower().strip()
        if full_query and full_query in haystacks["label"]:
            score += 6
            reasons.append("full query found in label")
        elif full_query and full_query in haystacks["context"]:
            score += 4
            reasons.append("full query found in context")

        # --- Visibility bonus ---
        if e.get("inView"):
            score += 3
            reasons.append("currently visible")

        # --- A11y quality bonus: aria-label = element was designed for accessibility ---
        if label.strip():
            score += 2
            reasons.append("has aria-label")

        # --- Action type fit ---
        if action == "type":
            if etype in ("search", "text", "email", "password", "tel", "url") or "search" in haystacks["meta"]:
                score += 4
                reasons.append("text-input friendly")
            if placeholder:
                score += 2
                reasons.append("has placeholder")
        else:
            if tag in ("button", "a", "input", "select") or role in ("button", "link", "option"):
                score += 2
                reasons.append("interactive element")
            # Icon/short-text controls: valid if they have nearby context
            label_or_text = (label or text).strip()
            if len(label_or_text) <= 3 and (context or heading):
                score += 2
                reasons.append("icon control with nearby context")

        # --- Penalties ---
        if not (label or text or placeholder or context):
            score -= 3

        # --- href relevance for links ---
        if tag == "a" and href and any(t in href.lower() for t in q_terms):
            score += 2
            reasons.append("href matches query")

        ranked.append(
            {
                "element": e,
                "score": score,
                "confidence": _confidence(score),
                "reason": ", ".join(dict.fromkeys(reasons)) or "weak DOM evidence",
                "source": "DOM",
            }
        )

    ranked.sort(key=lambda item: (item["score"], bool(item["element"].get("inView"))), reverse=True)
    return ranked[: max(1, min(max_results, 20))]


def _rank_generic_dom_candidates(candidates: list[dict], query: str, max_results: int) -> list[dict]:
    q_terms = _terms(query)
    scored: list[dict] = []
    for c in candidates:
        hay = " ".join(
            str(c.get(k, "")).lower()
            for k in ("text", "aria", "placeholder", "testid", "href", "tag", "role", "selector")
        )
        score = 0
        for term in q_terms:
            if term in hay:
                score += 3
        if c.get("inView"):
            score += 1
        if c.get("tag") in ("button", "input", "a", "select"):
            score += 1
        scored.append({"candidate": c, "score": score})
    scored.sort(key=lambda item: item["score"], reverse=True)
    positive = [item for item in scored if item["score"] > 0]
    if positive:
        return positive[: max(1, min(max_results, 30))]
    return scored[: max(1, min(max_results, 10))]
