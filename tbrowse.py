#!/usr/bin/env python3
"""
tbrowse — Interactive terminal web browser (curses UI)

Usage:
  tbrowse                        # Open browser prompt
  tbrowse "search query"         # Search directly
  tbrowse https://example.com    # Open URL directly

Keys:
  j / DOWN      Scroll down one line
  k / UP        Scroll up one line
  d / PgDn      Scroll down half page
  u / PgUp      Scroll up half page
  g / Home      Go to top
  G / End       Go to bottom
  Tab / n       Next link
  N             Previous link
  Enter         Follow selected link
  o / /         Open URL or search prompt (edits URL bar)
  b             Go back
  h             View history
  r             Reload page
  a             Toggle AI Overview on/off
  q             Quit
"""
from __future__ import annotations
import subprocess, sys, os

# ── Dependency bootstrap ──────────────────────────────────────────────────────
def _pip(*args):
    subprocess.check_call(
        [sys.executable, "-m", "pip", "install", "--quiet"] + list(args),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )

def _ensure(pkg, import_name):
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False

def _install(pkg, import_name):
    if _ensure(pkg, import_name):
        return
    for flags in [[], ["--break-system-packages"], ["--user"]]:
        try:
            _pip(pkg, *flags)
            if _ensure(pkg, import_name):
                return
        except subprocess.CalledProcessError:
            continue
    print(f"ERROR: Could not install '{pkg}'. Please run: pip install {pkg}")
    sys.exit(1)

_install("urllib3<2",      "urllib3")
_install("requests",       "requests")
_install("beautifulsoup4", "bs4")
_install("html2text",      "html2text")

# ── Search engine setup ───────────────────────────────────────────────────────
_search_engine = None

for _pkg, _mod in [("googlesearch-python", "googlesearch")]:
    if _ensure(_pkg, _mod):
        _search_engine = "google"
        break
if _search_engine is None:
    for _pkg, _mod in [("googlesearch-python", "googlesearch")]:
        for _flags in [[], ["--break-system-packages"], ["--user"]]:
            try:
                _pip(_pkg, *_flags)
                if _ensure(_pkg, _mod):
                    _search_engine = "google"
                    break
            except subprocess.CalledProcessError:
                continue
        if _search_engine:
            break

if _search_engine is None:
    for _pkg, _mod in [("ddgs", "ddgs"), ("duckduckgo-search", "duckduckgo_search")]:
        if _ensure(_pkg, _mod):
            _search_engine = _mod
            break
    if _search_engine is None:
        for _pkg, _mod in [("ddgs", "ddgs"), ("duckduckgo-search", "duckduckgo_search")]:
            for _flags in [[], ["--break-system-packages"], ["--user"]]:
                try:
                    _pip(_pkg, *_flags)
                    if _ensure(_pkg, _mod):
                        _search_engine = _mod
                        break
                except subprocess.CalledProcessError:
                    continue
            if _search_engine:
                break

if _search_engine is None:
    print("ERROR: Could not install any search library. Run: pip install googlesearch-python")
    sys.exit(1)

# ── Imports ───────────────────────────────────────────────────────────────────
import curses, re, textwrap, traceback, time
from urllib.parse import urljoin, urlparse, quote_plus
from typing import Optional, List, Tuple

import requests
from bs4 import BeautifulSoup
import html2text as h2t

# ── HTTP fetch ────────────────────────────────────────────────────────────────
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
}

def fetch(url: str) -> Tuple[Optional[str], str]:
    """Return (html, final_url) or (None, error_message)."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=14, allow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" not in ct:
            return None, f"Non-HTML content: {ct}"
        return r.text, r.url
    except requests.exceptions.ConnectionError:
        return None, "Connection error — check your network"
    except requests.exceptions.Timeout:
        return None, "Request timed out (14s)"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.reason}"
    except Exception as e:
        return None, str(e)

# ── AI Overview extractor ─────────────────────────────────────────────────────
def _extract_ai_overview(html: str) -> Optional[str]:
    """
    Try to extract the Google AI Overview (SGE) block from a raw Google SERP HTML.
    Returns a plain-text summary string, or None if not found.
    """
    if not html:
        return None

    soup = BeautifulSoup(html, "html.parser")

    # Strategy 1: look for the AI overview container by known data attributes / class patterns
    # Google uses several different class names and data tags over time — we try them all
    candidate_selectors = [
        # AI Overview block (2024+)
        "div[data-attrid='wa:/description']",
        "div[jsname='yEVEwb']",
        "div[jsname='Cpkphb']",
        "div[data-content-feature='1']",
        # Featured snippet (fallback – still very useful)
        "div.LGOjhe",
        "div.yDYNvb",
        "div[data-tts='answers']",
        "block-component",
        "div.wDYxhc",           # knowledge panel summary
        "div.kno-rdesc span",   # knowledge panel description
        "div.ifM9O",            # AI overview wrapper (newer)
        "div.cLjAic",
        "div.fm06If",
    ]

    for sel in candidate_selectors:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 60:
                return _clean_ai_text(text)

    # Strategy 2: look for any block that carries "AI Overview" as a heading sibling
    for tag in soup.find_all(["h2", "h3", "span", "div"]):
        t = tag.get_text(" ", strip=True).lower()
        if "ai overview" in t or "ai-powered overview" in t:
            # grab the next substantial sibling or parent's text
            parent = tag.find_parent()
            if parent:
                text = parent.get_text(" ", strip=True)
                if len(text) > 80:
                    return _clean_ai_text(text)

    # Strategy 3: look inside <script type="application/ld+json"> for description
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            import json
            data = json.loads(script.string or "")
            desc = None
            if isinstance(data, dict):
                desc = data.get("description") or data.get("abstract")
            if desc and len(desc) > 60:
                return _clean_ai_text(desc)
        except Exception:
            pass

    return None


def _clean_ai_text(text: str) -> str:
    """Normalize whitespace and remove boilerplate phrases from AI overview text."""
    text = re.sub(r"\s+", " ", text).strip()
    # Strip common leading junk
    for prefix in ["AI Overview", "AI-powered overview", "Overview", "Summary"]:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].lstrip(" :–—")
    # Remove trailing "More about …" type cruft
    text = re.sub(r"\s*(More about|Learn more|See more|Source[s]?).*$", "", text, flags=re.IGNORECASE)
    return text.strip()


# ── Google search ─────────────────────────────────────────────────────────────
def _google_search(query: str, num: int = 20) -> Tuple[List[dict], Optional[str]]:
    """
    Returns (results_list, ai_overview_text_or_None).
    """
    from googlesearch import search as gsearch
    results = []
    seen = set()
    ai_overview = None

    # First: scrape Google HTML for AI Overview + featured snippets
    raw_html, _ = fetch(f"https://www.google.com/search?q={quote_plus(query)}&num={num}&hl=en")
    if raw_html:
        ai_overview = _extract_ai_overview(raw_html)
        # Also pull organic results from the HTML as a reliable fallback
        html_results = _google_html_parse(raw_html, num)
        for r in html_results:
            if r["href"] not in seen:
                seen.add(r["href"])
                results.append(r)

    # Also try the library for any additional results / enrichment
    try:
        items = list(gsearch(query, num_results=num, sleep_interval=0.3, advanced=True))
        for item in items:
            if hasattr(item, "url"):
                url   = item.url
                title = getattr(item, "title", "") or url
                body  = getattr(item, "description", "") or ""
            else:
                url   = str(item)
                title = url
                body  = ""
            if url and url not in seen:
                seen.add(url)
                results.append({"title": title, "href": url, "body": body})
    except Exception:
        pass  # we already have HTML results

    return results, ai_overview


def _google_html_parse(html: str, num: int = 20) -> List[dict]:
    """Parse organic search results from a raw Google SERP page."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for g in soup.select("div.g, div[data-hveid]")[:num * 2]:
        a = g.find("a", href=True)
        if not a:
            continue
        href = a["href"]
        if href.startswith("/url?q="):
            href = href[7:].split("&")[0]
        if not href.startswith("http"):
            continue
        title_el = g.find("h3")
        title = title_el.get_text(" ", strip=True) if title_el else href
        snippet_el = g.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf], div.s3v9rd")
        body = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        if title and href:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= num:
            break
    return results


def _ddgs_search(query: str, num: int = 20) -> Tuple[List[dict], None]:
    """DuckDuckGo search fallback — no AI overview available."""
    try:
        if _search_engine == "ddgs":
            from ddgs import DDGS
        else:
            from duckduckgo_search import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=num)), None
    except Exception:
        return [], None


def search(query: str) -> Tuple[List[dict], Optional[str]]:
    """Dispatch to best available search engine. Returns (results, ai_overview)."""
    if _search_engine == "google":
        results, ai_ov = _google_search(query)
        if not results:
            results, _ = _ddgs_search(query)
        return results, ai_ov
    return _ddgs_search(query)


# ── HTML → plain lines + link map ────────────────────────────────────────────
def parse_page(html: str, base_url: str) -> Tuple[List[str], List[Tuple[int, str, str]]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "iframe", "svg",
                     "nav", "footer", "header", "aside", "form",
                     "button", "input", "select", "textarea", "meta", "link"]):
        tag.decompose()

    MARK = "\x00L\x00"
    link_data: List[Tuple[str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("#") or href.startswith("javascript"):
            a.unwrap()
            continue
        full = urljoin(base_url, href)
        txt  = a.get_text(" ", strip=True)[:80] or full
        link_data.append((txt, full))
        a.replace_with(f"{MARK}{len(link_data)-1}{MARK}{txt}{MARK}")

    conv = h2t.HTML2Text()
    conv.ignore_links  = True
    conv.ignore_images = True
    conv.body_width    = 0
    conv.unicode_snob  = True
    raw = conv.handle(str(soup))

    lines: List[str] = []
    links: List[Tuple[int, str, str]] = []
    wrap_w = 100

    for raw_line in raw.splitlines():
        if MARK in raw_line:
            parts = raw_line.split(MARK)
            clean = []
            i = 0
            while i < len(parts):
                if parts[i].isdigit() and i > 0 and parts[i-1] == "":
                    idx     = int(parts[i])
                    display = parts[i+1] if i+1 < len(parts) else ""
                    links.append((len(lines), display, link_data[idx][1]))
                    clean.append(f"[{len(links)}] {display}")
                    i += 2
                else:
                    if parts[i] != "":
                        clean.append(parts[i])
                    i += 1
            raw_line = "".join(clean)

        s = raw_line.rstrip()
        if s.startswith("### "):
            lines.append("  " + s[4:].upper())
        elif s.startswith("## "):
            lines.append("  " + s[3:].upper())
        elif s.startswith("# "):
            lines.append("  " + s[2:].upper())
        elif s.startswith("* ") or s.startswith("- "):
            for wl in textwrap.wrap(s[2:], wrap_w - 4) or [""]:
                lines.append("  • " + wl)
        elif s.startswith("> "):
            lines.append("  │ " + s[2:])
        elif s.startswith("    "):
            lines.append(s)
        elif s == "" or set(s.strip()) <= {"─", "—", "-", "=", "*"}:
            lines.append("")
        else:
            for wl in textwrap.wrap(s, wrap_w) or [""]:
                lines.append("  " + wl)

    out: List[str] = []
    prev_blank = False
    for ln in lines:
        blank = ln.strip() == ""
        if blank and prev_blank:
            continue
        out.append(ln)
        prev_blank = blank
    return out, links


# ── Search result page builder ────────────────────────────────────────────────
# Special sentinel used to mark AI overview lines (for colour coding)
_AI_LINE_PREFIX = "\x01AI\x01"
_AI_SOURCE_PREFIX = "\x01SRC\x01"

def build_search_page(
    results: List[dict],
    query: str,
    engine: str = "",
    ai_overview: Optional[str] = None,
    show_ai: bool = True,
) -> Tuple[List[str], List[Tuple[int, str, str]]]:
    engine_label = "Google" if engine == "google" else "DuckDuckGo"
    lines: List[str] = [
        "",
        f"  {engine_label} Search: {query}",
        "  " + "━" * 60,
        "",
    ]
    links: List[Tuple[int, str, str]] = []

    # ── AI Overview block ─────────────────────────────────────────────────────
    if show_ai and ai_overview:
        ai_search_url = f"https://www.google.com/search?q={quote_plus(query)}"
        lines.append(_AI_LINE_PREFIX + "  ✦  AI OVERVIEW")
        lines.append(_AI_LINE_PREFIX + "  " + "─" * 58)
        # Word-wrap the overview text
        for para in ai_overview.split("\n"):
            para = para.strip()
            if not para:
                continue
            for wl in textwrap.wrap(para, 86) or [""]:
                lines.append(_AI_LINE_PREFIX + "  " + wl)
        lines.append(_AI_SOURCE_PREFIX + f"  ↗  View on Google: {ai_search_url}")
        links.append((len(lines) - 1, "View AI Overview on Google", ai_search_url))
        lines.append(_AI_LINE_PREFIX + "  " + "─" * 58)
        lines.append("")
    elif show_ai and engine == "google":
        lines.append(_AI_LINE_PREFIX + "  ✦  AI OVERVIEW  (not available for this query)")
        lines.append("")

    # ── Organic results ───────────────────────────────────────────────────────
    for i, r in enumerate(results, 1):
        title       = r.get("title", "No title")
        url         = r.get("href", "")
        body        = r.get("body", "")
        display_url = url if len(url) <= 72 else url[:69] + "…"

        links.append((len(lines), title, url))
        lines.append(f"  [{i}] {title}")
        lines.append(f"       {display_url}")
        if body:
            body_clean = re.sub(r"\s+", " ", body).strip()
            for wl in textwrap.wrap(body_clean, 86):
                lines.append(f"       {wl}")
        lines.append("")

    return lines, links


# ── Curses Browser ────────────────────────────────────────────────────────────
class Browser:
    URL_ROW    = 0
    STATUS_ROW = 1
    HELP_ROW   = -1

    def __init__(self, stdscr: curses.window):
        self.scr    = stdscr
        self.lines: List[str]                                = []
        self.links: List[Tuple[int, str, str]]               = []
        self.history: List[Tuple[str, List[str], list, int]] = []
        self.scroll      = 0
        self.sel_link    = -1
        self.url         = ""
        self.status_msg  = ""
        self.last_engine = _search_engine
        # AI overview state
        self.show_ai        = True
        self._last_query    = ""
        self._last_results: List[dict]      = []
        self._last_ai_ov:   Optional[str]   = None
        self._setup_colors()

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1,  curses.COLOR_WHITE,  curses.COLOR_BLUE)   # URL bar
        curses.init_pair(2,  curses.COLOR_YELLOW, -1)                  # headings
        curses.init_pair(3,  curses.COLOR_CYAN,   -1)                  # link normal
        curses.init_pair(4,  curses.COLOR_BLACK,  curses.COLOR_GREEN)  # link selected
        curses.init_pair(5,  curses.COLOR_WHITE,  -1)                  # dim / quote
        curses.init_pair(6,  curses.COLOR_RED,    -1)                  # error
        curses.init_pair(7,  curses.COLOR_BLACK,  curses.COLOR_WHITE)  # input prompt
        curses.init_pair(8,  curses.COLOR_BLACK,  curses.COLOR_CYAN)   # status bar
        curses.init_pair(9,  curses.COLOR_YELLOW, curses.COLOR_BLUE)   # URL bar label
        curses.init_pair(10, curses.COLOR_GREEN,  -1)                  # search URL
        # New: AI Overview colours
        curses.init_pair(11, curses.COLOR_BLACK,  curses.COLOR_YELLOW) # AI header
        curses.init_pair(12, curses.COLOR_YELLOW, -1)                  # AI body text
        curses.init_pair(13, curses.COLOR_GREEN,  -1)                  # AI source link

    def _rows(self): return self.scr.getmaxyx()[0]
    def _cols(self): return self.scr.getmaxyx()[1]
    def _view_h(self): return max(1, self._rows() - 3)

    # ── drawing ───────────────────────────────────────────────────────────────
    def _draw_urlbar(self):
        cols = self._cols()
        label       = " ❯ "
        url_display = self.url or "about:blank"
        lcount      = f" {len(self.links)} links " if self.links else ""
        avail       = cols - len(label) - len(lcount)
        if len(url_display) > avail:
            url_display = "…" + url_display[-(avail - 1):]
        bar = label + url_display.ljust(avail) + lcount
        try:
            self.scr.attron(curses.color_pair(1))
            self.scr.addstr(0, 0, bar[:cols].ljust(cols))
            self.scr.attroff(curses.color_pair(1))
            self.scr.attron(curses.color_pair(9) | curses.A_BOLD)
            self.scr.addstr(0, 0, label)
            self.scr.attroff(curses.color_pair(9) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_statusbar(self):
        cols = self._cols()
        if self.status_msg:
            msg  = f" {self.status_msg} "
            line = msg[:cols].ljust(cols)
        else:
            total = max(1, len(self.lines))
            pct   = int(100 * (self.scroll + self._view_h()) / total)
            pct   = min(pct, 100)
            pos   = f" {self.scroll + 1}-{min(self.scroll + self._view_h(), total)}/{total} ({pct}%) "
            back  = f" ← {len(self.history)} " if self.history else ""
            # Show AI status indicator
            ai_indicator = " [AI✦ON] " if self.show_ai else " [AI✦OFF] "
            title_area   = cols - len(pos) - len(back) - len(ai_indicator)
            line = (back + " " * max(0, title_area - len(back)) + ai_indicator + pos)[:cols].ljust(cols)
        try:
            self.scr.attron(curses.color_pair(8))
            self.scr.addstr(1, 0, line[:cols])
            self.scr.attroff(curses.color_pair(8))
        except curses.error:
            pass

    def _draw_helpbar(self):
        cols = self._cols()
        rows = self._rows()
        msg  = "  j/k scroll  Tab link  Enter open  o URL  b back  h hist  a AI-toggle  r reload  q quit"
        try:
            self.scr.attron(curses.color_pair(7))
            self.scr.addstr(rows - 1, 0, msg[:cols].ljust(cols))
            self.scr.attroff(curses.color_pair(7))
        except curses.error:
            pass

    def _draw_content(self):
        cols   = self._cols()
        view_h = self._view_h()
        link_line_map = {li: i for i, (li, _, _) in enumerate(self.links)}

        for row in range(view_h):
            li      = self.scroll + row
            scr_row = row + 2
            try:
                self.scr.move(scr_row, 0)
                self.scr.clrtoeol()
            except curses.error:
                pass
            if li >= len(self.lines):
                continue

            raw_text = self.lines[li]
            llink    = link_line_map.get(li, -1)
            is_sel   = (llink == self.sel_link and self.sel_link >= 0)

            # Determine if this is an AI overview line
            is_ai_header = raw_text.startswith(_AI_LINE_PREFIX)
            is_ai_src    = raw_text.startswith(_AI_SOURCE_PREFIX)
            # Strip prefix for display
            if is_ai_header:
                text = raw_text[len(_AI_LINE_PREFIX):]
            elif is_ai_src:
                text = raw_text[len(_AI_SOURCE_PREFIX):]
            else:
                text = raw_text

            s = text.strip()

            if is_sel:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif is_ai_src:
                attr = curses.color_pair(13) | curses.A_UNDERLINE
            elif is_ai_header:
                if "AI OVERVIEW" in s.upper() and "✦" in s:
                    attr = curses.color_pair(11) | curses.A_BOLD
                else:
                    attr = curses.color_pair(12)
            elif s.isupper() and len(s) > 2 and not s.startswith("["):
                attr = curses.color_pair(2) | curses.A_BOLD
            elif s.startswith("[") and "]" in s:
                attr = curses.color_pair(3)
            elif s.startswith("http") and " " not in s:
                attr = curses.color_pair(10)
            elif s.startswith("│") or s.startswith("•"):
                attr = curses.color_pair(5)
            else:
                attr = curses.A_NORMAL

            try:
                self.scr.attron(attr)
                display = text[:cols - 1]
                if is_sel:
                    display = display.ljust(cols - 1)
                self.scr.addstr(scr_row, 0, display)
                self.scr.attroff(attr)
            except curses.error:
                pass

    def draw(self):
        self.scr.erase()
        self._draw_urlbar()
        self._draw_statusbar()
        self._draw_content()
        self._draw_helpbar()
        self.scr.refresh()

    # ── scrolling ─────────────────────────────────────────────────────────────
    def scroll_by(self, delta: int):
        max_s = max(0, len(self.lines) - self._view_h())
        self.scroll = max(0, min(self.scroll + delta, max_s))

    def scroll_to(self, line: int):
        max_s = max(0, len(self.lines) - self._view_h())
        self.scroll = max(0, min(line, max_s))

    # ── link navigation ───────────────────────────────────────────────────────
    def next_link(self, direction: int = 1):
        if not self.links:
            return
        self.sel_link = (self.sel_link + direction) % len(self.links) if self.sel_link >= 0 else 0
        li = self.links[self.sel_link][0]
        if li < self.scroll or li >= self.scroll + self._view_h():
            self.scroll_to(max(0, li - self._view_h() // 2))

    # ── URL bar inline edit ───────────────────────────────────────────────────
    def _edit_urlbar(self, prefill: str = "") -> str:
        cols    = self._cols()
        prompt  = " ❯ "
        field_w = cols - len(prompt) - 1
        buf     = list(prefill or self.url or "")
        cur     = len(buf)

        curses.echo()
        curses.curs_set(1)

        def _render():
            start    = max(0, cur - field_w + 1)
            view     = buf[start:start + field_w]
            view_str = "".join(view).ljust(field_w)
            try:
                self.scr.attron(curses.color_pair(7) | curses.A_BOLD)
                self.scr.addstr(0, 0, prompt)
                self.scr.attroff(curses.color_pair(7) | curses.A_BOLD)
                self.scr.attron(curses.color_pair(7))
                self.scr.addstr(0, len(prompt), view_str[:field_w])
                self.scr.attroff(curses.color_pair(7))
                self.scr.move(0, len(prompt) + (cur - start))
            except curses.error:
                pass
            self.scr.refresh()

        curses.noecho()
        result = None
        try:
            while True:
                _render()
                ch = self.scr.getch()
                if ch in (10, 13, curses.KEY_ENTER):
                    result = "".join(buf).strip()
                    break
                elif ch == 27:
                    result = ""
                    break
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    if cur > 0:
                        del buf[cur - 1]
                        cur -= 1
                elif ch == curses.KEY_DC:
                    if cur < len(buf):
                        del buf[cur]
                elif ch == curses.KEY_LEFT:
                    cur = max(0, cur - 1)
                elif ch == curses.KEY_RIGHT:
                    cur = min(len(buf), cur + 1)
                elif ch == curses.KEY_HOME:
                    cur = 0
                elif ch == curses.KEY_END:
                    cur = len(buf)
                elif 32 <= ch <= 126:
                    buf.insert(cur, chr(ch))
                    cur += 1
        finally:
            curses.curs_set(0)
        return result or ""

    # ── loading indicator ─────────────────────────────────────────────────────
    def _loading(self, msg: str):
        cols = self._cols()
        try:
            self.scr.attron(curses.color_pair(8) | curses.A_BOLD)
            self.scr.addstr(1, 0, f"  ⏳ {msg} "[:cols].ljust(cols))
            self.scr.attroff(curses.color_pair(8) | curses.A_BOLD)
            self.scr.refresh()
        except curses.error:
            pass

    # ── page loading ──────────────────────────────────────────────────────────
    def load_url(self, url: str, push_history: bool = True):
        if push_history and self.lines:
            self.history.append((self.url, self.lines[:], self.links[:], self.scroll))
        self._loading(f"Loading {url[:70]}…")
        html, final_url = fetch(url)
        if html is None:
            self.lines = [
                "", f"  ✗  {final_url}", "",
                f"  Could not load:", f"  {url}",
            ]
            self.links = []
            self.url   = url
        else:
            self.lines, self.links = parse_page(html, final_url)
            self.url = final_url
        self.scroll   = 0
        self.sel_link = -1
        self.status_msg = ""

    def load_search(self, query: str, push_history: bool = True):
        if push_history and self.lines:
            self.history.append((self.url, self.lines[:], self.links[:], self.scroll))
        engine_label = "Google" if _search_engine == "google" else "DuckDuckGo"
        self._loading(f"Searching {engine_label}: {query[:55]}…")

        results, ai_overview = search(query)

        # Cache for AI toggle
        self._last_query   = query
        self._last_results = results
        self._last_ai_ov   = ai_overview

        if not results:
            self.lines = ["", "  No results found.", "", "  Try a different query."]
            self.links = []
        else:
            self.lines, self.links = build_search_page(
                results, query, _search_engine,
                ai_overview=ai_overview,
                show_ai=self.show_ai,
            )
        self.url        = f"search: {query}"
        self.scroll     = 0
        self.sel_link   = -1
        ai_note         = " ✦AI" if (ai_overview and self.show_ai) else ""
        self.status_msg = (
            f"{len(results)} results via {engine_label}{ai_note}" if results else ""
        )

    def toggle_ai(self):
        """Toggle AI overview visibility and re-render the search page."""
        self.show_ai = not self.show_ai
        if self._last_query and self._last_results:
            self.lines, self.links = build_search_page(
                self._last_results,
                self._last_query,
                _search_engine,
                ai_overview=self._last_ai_ov,
                show_ai=self.show_ai,
            )
            engine_label = "Google" if _search_engine == "google" else "DuckDuckGo"
            ai_note      = " ✦AI" if (self._last_ai_ov and self.show_ai) else ""
            self.status_msg = (
                f"{len(self._last_results)} results via {engine_label}{ai_note}"
            )
            self.scroll   = 0
            self.sel_link = -1
        else:
            self.status_msg = f"AI Overview: {'ON' if self.show_ai else 'OFF'}"

    def go_back(self):
        if not self.history:
            self.status_msg = "No history"
            return
        self.url, self.lines, self.links, self.scroll = self.history.pop()
        self.sel_link   = -1
        self.status_msg = ""

    def show_history(self):
        if not self.history:
            self.lines = ["", "  No history yet."]
            self.links = []
        else:
            self.lines = ["", "  BROWSING HISTORY", "  " + "━" * 50, ""]
            self.links = []
            for i, (u, _, _, _) in enumerate(reversed(self.history), 1):
                self.links.append((len(self.lines), u, u))
                display = u if len(u) <= 90 else u[:87] + "…"
                self.lines.append(f"  [{i}]  {display}")
                self.lines.append("")
        self.scroll    = 0
        self.sel_link  = -1
        self.url       = "history"
        self.status_msg = ""

    def open_prompt(self, prefill: str = ""):
        query = self._edit_urlbar(prefill)
        if not query:
            return
        if re.match(r"^https?://", query):
            self.load_url(query)
        elif re.match(r"^[\w-]+\.[a-z]{2,}", query) and " " not in query:
            self.load_url("https://" + query)
        else:
            self.load_search(query)

    # ── main event loop ───────────────────────────────────────────────────────
    def run(self):
        curses.curs_set(0)
        curses.mousemask(curses.ALL_MOUSE_EVENTS)

        if not self.lines:
            self.lines = [
                "",
                "  ████████╗██████╗ ██████╗  ██████╗ ██╗    ██╗███████╗███████╗",
                "     ██╔══╝██╔══██╗██╔══██╗██╔═══██╗██║    ██║██╔════╝██╔════╝",
                "     ██║   ██████╔╝██████╔╝██║   ██║██║ █╗ ██║███████╗█████╗  ",
                "     ██║   ██╔══██╗██╔══██╗██║   ██║██║███╗██║╚════██║██╔══╝  ",
                "     ██║   ██████╔╝██║  ██║╚██████╔╝╚███╔███╔╝███████║███████╗",
                "     ╚═╝   ╚═════╝ ╚═╝  ╚═╝ ╚═════╝  ╚══╝╚══╝ ╚══════╝╚══════╝",
                "",
                "  Terminal Browser  —  press  o  to open a URL or search",
                "",
                "  Quick keys:",
                "    o  /  /     →  open URL bar (type URL or search query)",
                "    Tab / n     →  next link",
                "    Enter       →  follow link",
                "    a           →  toggle AI Overview on / off",
                "    b           →  go back",
                "    h           →  history",
                "    j / k       →  scroll",
                "    q           →  quit",
                "",
                "  ✦  AI Overviews will appear above search results when available.",
                "",
            ]
            self.url = "tbrowse — Terminal Browser"

        while True:
            self.draw()
            try:
                key = self.scr.getch()
            except KeyboardInterrupt:
                break

            vh = self._view_h()

            if key in (ord("q"), ord("Q")):
                break
            elif key in (ord("j"), curses.KEY_DOWN):
                self.scroll_by(1)
            elif key in (ord("k"), curses.KEY_UP):
                self.scroll_by(-1)
            elif key in (ord("d"), curses.KEY_NPAGE):
                self.scroll_by(vh // 2)
            elif key in (ord("u"), curses.KEY_PPAGE):
                self.scroll_by(-(vh // 2))
            elif key in (ord("g"), curses.KEY_HOME):
                self.scroll = 0
            elif key in (ord("G"), curses.KEY_END):
                self.scroll_to(len(self.lines))
            elif key in (9, ord("n"), curses.KEY_RIGHT):
                self.next_link(1)
            elif key in (ord("N"), curses.KEY_LEFT):
                self.next_link(-1)
            elif key in (10, 13, curses.KEY_ENTER):
                if self.sel_link >= 0 and self.sel_link < len(self.links):
                    _, _, url = self.links[self.sel_link]
                    self.load_url(url)
                else:
                    self.open_prompt()
            elif key in (ord("o"), ord("/")):
                self.open_prompt()
            elif key in (ord("b"), curses.KEY_BACKSPACE, 127):
                self.go_back()
            elif key == ord("h"):
                self.show_history()
            elif key == ord("a"):
                self.toggle_ai()
            elif key == ord("r"):
                if self.url and not self.url.startswith("search:"):
                    self.load_url(self.url, push_history=False)
                elif self.url.startswith("search:"):
                    q = self.url[7:].strip()
                    self.load_search(q, push_history=False)
            elif 0 <= key <= 127 and chr(key).isdigit():
                num_str = chr(key)
                self.scr.nodelay(True)
                while True:
                    nx = self.scr.getch()
                    if nx != -1 and 0 <= nx <= 127 and chr(nx).isdigit():
                        num_str += chr(nx)
                    else:
                        break
                self.scr.nodelay(False)
                n = int(num_str) - 1
                if 0 <= n < len(self.links):
                    self.load_url(self.links[n][2])
            elif key == curses.KEY_MOUSE:
                try:
                    _, mx, my, _, bstate = curses.getmouse()
                    if bstate & curses.BUTTON4_PRESSED:
                        self.scroll_by(-3)
                    elif bstate & curses.BUTTON5_PRESSED:
                        self.scroll_by(3)
                    elif bstate & curses.BUTTON1_CLICKED:
                        if my == 0:
                            self.open_prompt()
                except curses.error:
                    pass
            elif key == curses.KEY_RESIZE:
                self.scr.clear()


# ── Entry point ───────────────────────────────────────────────────────────────
def main(stdscr: curses.window):
    browser = Browser(stdscr)
    args = sys.argv[1:]
    if args:
        arg = " ".join(args)
        if re.match(r"^https?://", arg):
            browser.load_url(arg, push_history=False)
        elif re.match(r"^[\w-]+\.[a-z]{2,}", arg) and " " not in arg:
            browser.load_url("https://" + arg, push_history=False)
        else:
            browser.load_search(arg, push_history=False)
    browser.run()


if __name__ == "__main__":
    try:
        curses.wrapper(main)
    except KeyboardInterrupt:
        pass
    except Exception as e:
        print(f"\nCrash: {e}")
        traceback.print_exc()
