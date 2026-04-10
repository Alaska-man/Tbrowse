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
# Priority: googlesearch-python (scrapes Google) → ddgs → duckduckgo-search
_search_engine = None

# Try googlesearch-python first (real Google results)
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

# Fall back to ddgs / duckduckgo-search
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
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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

# ── Google search (scrapes Google via googlesearch-python) ───────────────────
def _google_search(query: str, num: int = 20) -> List[dict]:
    """
    Returns list of dicts with keys: title, href, body.
    Uses googlesearch-python which scrapes real Google SERPs.
    Falls back to fetching Google's result page for snippets.
    """
    from googlesearch import search as gsearch
    results = []
    seen = set()
    try:
        # googlesearch-python yields URLs; we enrich with titles/snippets
        urls = list(gsearch(query, num_results=num, sleep_interval=0.5, advanced=True))
        for item in urls:
            if hasattr(item, "url"):
                url = item.url
                title = getattr(item, "title", "") or url
                body = getattr(item, "description", "") or ""
            else:
                url = str(item)
                title = url
                body = ""
            if url and url not in seen:
                seen.add(url)
                results.append({"title": title, "href": url, "body": body})
    except Exception:
        # Fallback: fetch Google HTML directly
        results = _google_html_fallback(query, num)
    return results

def _google_html_fallback(query: str, num: int = 20) -> List[dict]:
    """Scrape Google search result page as a fallback."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={num}"
    html, _ = fetch(url)
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    for g in soup.select("div.g, div[data-hveid]")[:num]:
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
        snippet_el = g.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf]")
        body = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append({"title": title, "href": href, "body": body})
    return results

def _ddgs_search(query: str, num: int = 20) -> List[dict]:
    """DuckDuckGo search fallback."""
    try:
        if _search_engine == "ddgs":
            from ddgs import DDGS
        else:
            from duckduckgo_search import DDGS
        with DDGS() as d:
            return list(d.text(query, max_results=num))
    except Exception:
        return []

def search(query: str) -> List[dict]:
    """Dispatch to best available search engine."""
    if _search_engine == "google":
        results = _google_search(query)
        if not results:
            results = _ddgs_search(query)   # cascade if Google blocked
        return results
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


def build_search_page(results: List[dict], query: str, engine: str = "") -> Tuple[List[str], List[Tuple[int, str, str]]]:
    engine_label = f"Google" if engine == "google" else "DuckDuckGo"
    lines: List[str] = [
        "",
        f"  {engine_label} Search: {query}",
        "  " + "━" * 60,
        "",
    ]
    links: List[Tuple[int, str, str]] = []
    for i, r in enumerate(results, 1):
        title = r.get("title", "No title")
        url   = r.get("href", "")
        body  = r.get("body", "")

        # Truncate long URLs for display
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
    # Row layout:
    #   0         → URL bar
    #   1         → status / info bar
    #   2..rows-2 → content
    #   rows-1    → help bar / prompt

    URL_ROW    = 0
    STATUS_ROW = 1
    HELP_ROW   = -1   # last row (computed at draw time)

    def __init__(self, stdscr: curses.window):
        self.scr    = stdscr
        self.lines: List[str]                              = []
        self.links: List[Tuple[int, str, str]]             = []
        self.history: List[Tuple[str, List[str], list, int]] = []
        self.scroll   = 0
        self.sel_link = -1
        self.url      = ""
        self.status_msg = ""   # transient status message
        self.last_engine = _search_engine
        self._setup_colors()

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        # Pair 1: URL bar                   white on blue
        curses.init_pair(1, curses.COLOR_WHITE,  curses.COLOR_BLUE)
        # Pair 2: headings                  yellow bold
        curses.init_pair(2, curses.COLOR_YELLOW, -1)
        # Pair 3: link (normal)             cyan
        curses.init_pair(3, curses.COLOR_CYAN,   -1)
        # Pair 4: link (selected)           black on green
        curses.init_pair(4, curses.COLOR_BLACK,  curses.COLOR_GREEN)
        # Pair 5: dim / quote               white dim
        curses.init_pair(5, curses.COLOR_WHITE,  -1)
        # Pair 6: error                     red bold
        curses.init_pair(6, curses.COLOR_RED,    -1)
        # Pair 7: input prompt              black on white
        curses.init_pair(7, curses.COLOR_BLACK,  curses.COLOR_WHITE)
        # Pair 8: status bar                black on cyan
        curses.init_pair(8, curses.COLOR_BLACK,  curses.COLOR_CYAN)
        # Pair 9: URL bar label             yellow on blue
        curses.init_pair(9, curses.COLOR_YELLOW, curses.COLOR_BLUE)
        # Pair 10: search result URL        green dim
        curses.init_pair(10, curses.COLOR_GREEN, -1)

    def _rows(self): return self.scr.getmaxyx()[0]
    def _cols(self): return self.scr.getmaxyx()[1]
    def _view_h(self): return max(1, self._rows() - 3)   # URL + status + help = 3 rows

    # ── drawing ───────────────────────────────────────────────────────────────

    def _draw_urlbar(self):
        """Row 0: [ tbrowse ] https://... [back N]"""
        cols = self._cols()
        # Left label
        label = " ❯ "
        url_display = self.url or "about:blank"
        # Right side: link count
        lcount = f" {len(self.links)} links " if self.links else ""
        avail = cols - len(label) - len(lcount)
        if len(url_display) > avail:
            url_display = "…" + url_display[-(avail - 1):]
        bar = label + url_display.ljust(avail) + lcount
        try:
            self.scr.attron(curses.color_pair(1))
            self.scr.addstr(0, 0, bar[:cols].ljust(cols))
            self.scr.attroff(curses.color_pair(1))
            # Colour the label differently
            self.scr.attron(curses.color_pair(9) | curses.A_BOLD)
            self.scr.addstr(0, 0, label)
            self.scr.attroff(curses.color_pair(9) | curses.A_BOLD)
        except curses.error:
            pass

    def _draw_statusbar(self):
        """Row 1: page info + scroll position."""
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
            title_area = cols - len(pos) - len(back)
            line  = (back + " " * (title_area - len(back)) + pos)[:cols].ljust(cols)
        try:
            self.scr.attron(curses.color_pair(8))
            self.scr.addstr(1, 0, line[:cols])
            self.scr.attroff(curses.color_pair(8))
        except curses.error:
            pass

    def _draw_helpbar(self):
        cols = self._cols()
        rows = self._rows()
        msg  = "  j/k scroll  Tab link  Enter open  o edit URL  b back  h history  r reload  q quit"
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
            li = self.scroll + row
            scr_row = row + 2    # offset: URL bar + status bar
            try:
                self.scr.move(scr_row, 0)
                self.scr.clrtoeol()
            except curses.error:
                pass
            if li >= len(self.lines):
                continue

            text  = self.lines[li]
            llink = link_line_map.get(li, -1)
            is_sel = (llink == self.sel_link and self.sel_link >= 0)
            s     = text.strip()

            if is_sel:
                attr = curses.color_pair(4) | curses.A_BOLD
            elif s.isupper() and len(s) > 2 and not s.startswith("["):
                attr = curses.color_pair(2) | curses.A_BOLD
            elif s.startswith("[") and "]" in s:
                attr = curses.color_pair(3)
            elif s.startswith("http") and " " not in s:
                attr = curses.color_pair(10)      # raw URL lines in search results
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
        """
        Turn row 0 into an editable text field.
        Returns the entered string or "" if cancelled.
        """
        cols = self._cols()
        prompt = " ❯ "
        field_w = cols - len(prompt) - 1

        buf = list(prefill or self.url or "")
        cur = len(buf)

        curses.echo()
        curses.curs_set(1)

        def _render():
            # Slide window so cursor is always visible
            start = max(0, cur - field_w + 1)
            view  = buf[start:start + field_w]
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

        curses.noecho()   # manual echo
        result = None
        try:
            while True:
                _render()
                ch = self.scr.getch()
                if ch in (10, 13, curses.KEY_ENTER):
                    result = "".join(buf).strip()
                    break
                elif ch == 27:          # ESC — cancel
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
        results = search(query)
        if not results:
            self.lines = [
                "", "  No results found.",
                "", "  Try a different query.",
            ]
            self.links = []
        else:
            self.lines, self.links = build_search_page(results, query, _search_engine)
        self.url      = f"search: {query}"
        self.scroll   = 0
        self.sel_link = -1
        self.status_msg = f"{len(results)} results via {engine_label}" if results else ""

    def go_back(self):
        if not self.history:
            self.status_msg = "No history"
            return
        self.url, self.lines, self.links, self.scroll = self.history.pop()
        self.sel_link  = -1
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
        """Activate the URL bar for editing."""
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
                "    b           →  go back",
                "    h           →  history",
                "    j / k       →  scroll",
                "    q           →  quit",
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
            elif key in (9, ord("n"), curses.KEY_RIGHT):      # Tab / n
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
                        if my == 0:           # click on URL bar → edit it
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
