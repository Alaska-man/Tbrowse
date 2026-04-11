#!/usr/bin/env python3
"""
tbrowse — Interactive terminal web browser (curses UI)

Usage:
  tbrowse                        # Open browser prompt
  tbrowse "search query"         # Search directly
  tbrowse https://example.com    # Open URL directly
  tbrowse --debug "query"        # Search with debug output (run outside curses)

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
  o / /         Open URL or search prompt
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

def _ensure(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False

def _install(pkg: str, import_name: str):
    if _ensure(import_name):
        return
    for flags in [[], ["--break-system-packages"], ["--user"]]:
        try:
            _pip(pkg, *flags)
            if _ensure(import_name):
                return
        except subprocess.CalledProcessError:
            continue
    print(f"ERROR: Could not install '{pkg}'. Please run: pip install {pkg}")
    sys.exit(1)

_install("requests",       "requests")
_install("beautifulsoup4", "bs4")
_install("html2text",      "html2text")

# Install both search backends; we'll use whichever works at runtime
for _pkg, _mod in [
    ("ddgs",                 "ddgs"),
    ("duckduckgo-search",    "duckduckgo_search"),
    ("googlesearch-python",  "googlesearch"),
]:
    if not _ensure(_mod):
        for _flags in [[], ["--break-system-packages"], ["--user"]]:
            try:
                _pip(_pkg, *_flags)
                if _ensure(_mod):
                    break
            except subprocess.CalledProcessError:
                continue

# ── Imports ───────────────────────────────────────────────────────────────────
import curses, re, textwrap, traceback, time, json
from urllib.parse import urljoin, urlparse, quote_plus, unquote
from typing import Optional, List, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from bs4 import BeautifulSoup
import html2text as h2t

# ── Debug flag ────────────────────────────────────────────────────────────────
_DEBUG = "--debug" in sys.argv
if _DEBUG:
    sys.argv.remove("--debug")

def _dbg(msg: str):
    if _DEBUG:
        print(f"[DEBUG] {msg}", flush=True)

# ── HTTP session (shared, with retries) ───────────────────────────────────────
def _make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=0.4,
                  status_forcelist=[429, 500, 502, 503, 504])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://",  HTTPAdapter(max_retries=retry))
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
    })
    return s

SESSION = _make_session()

def fetch(url: str) -> Tuple[Optional[str], str]:
    """Return (html, final_url) or (None, error_message)."""
    try:
        r = SESSION.get(url, timeout=14, allow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" not in ct:
            return None, f"Non-HTML content: {ct}"
        return r.text, r.url
    except requests.exceptions.ConnectionError as e:
        return None, f"Connection error: {e}"
    except requests.exceptions.Timeout:
        return None, "Request timed out (14s)"
    except requests.exceptions.HTTPError as e:
        return None, f"HTTP {e.response.status_code}: {e.response.reason}"
    except Exception as e:
        return None, str(e)

# ── Search backends ───────────────────────────────────────────────────────────

def _search_ddgs(query: str, num: int = 20) -> List[dict]:
    """DuckDuckGo via the ddgs or duckduckgo_search library."""
    if _ensure("ddgs"):
        try:
            from ddgs import DDGS
            _dbg("Using ddgs library")
            with DDGS() as d:
                results = list(d.text(query, max_results=num))
                _dbg(f"ddgs returned {len(results)} results")
                return results
        except Exception as e:
            _dbg(f"ddgs failed: {e}")

    if _ensure("duckduckgo_search"):
        try:
            from duckduckgo_search import DDGS
            _dbg("Using duckduckgo_search library")
            with DDGS() as d:
                results = list(d.text(query, max_results=num))
                _dbg(f"duckduckgo_search returned {len(results)} results")
                return results
        except Exception as e:
            _dbg(f"duckduckgo_search failed: {e}")

    _dbg("All DDGS library backends failed")
    return []


def _search_ddg_html(query: str, num: int = 20) -> List[dict]:
    """
    Scrape DuckDuckGo's no-JS HTML endpoint directly.
    Very reliable — works even when the library is broken.
    """
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    _dbg(f"Trying DDG HTML scrape: {url}")
    try:
        r = SESSION.get(url, timeout=14)
        r.raise_for_status()
    except Exception as e:
        _dbg(f"DDG HTML fetch failed: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    results = []
    for res in soup.select("div.result")[:num]:
        title_el = res.select_one("a.result__a")
        snip_el  = res.select_one("a.result__snippet")
        if not title_el:
            continue
        href = title_el.get("href", "")
        # DDG wraps links via redirect — extract the real URL
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            href = unquote(m.group(1))
        if not href.startswith("http"):
            continue
        results.append({
            "title": title_el.get_text(" ", strip=True),
            "href":  href,
            "body":  snip_el.get_text(" ", strip=True) if snip_el else "",
        })
    _dbg(f"DDG HTML scrape returned {len(results)} results")
    return results


def _search_google_html(query: str, num: int = 20) -> Tuple[List[dict], Optional[str]]:
    """Scrape Google search HTML. Returns (results, ai_overview_or_None)."""
    url = f"https://www.google.com/search?q={quote_plus(query)}&num={num}&hl=en"
    _dbg(f"Trying Google HTML scrape: {url}")
    html, _ = fetch(url)
    if not html:
        _dbg("Google HTML fetch returned nothing")
        return [], None
    if "captcha" in html.lower() or "consent.google" in html.lower():
        _dbg("Google returned CAPTCHA/consent page — blocked")
        return [], None

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
        title    = title_el.get_text(" ", strip=True) if title_el else href
        snip_el  = g.select_one("div.VwiC3b, span.aCOpRe, div[data-sncf], div.s3v9rd")
        body     = snip_el.get_text(" ", strip=True) if snip_el else ""
        if title and href:
            results.append({"title": title, "href": href, "body": body})
        if len(results) >= num:
            break

    ai_overview = _extract_ai_overview(html) if results else None
    _dbg(f"Google HTML scrape returned {len(results)} results, AI={bool(ai_overview)}")
    return results, ai_overview


def _search_google_lib(query: str, num: int = 20) -> List[dict]:
    """googlesearch-python — often rate-limited, last resort."""
    if not _ensure("googlesearch"):
        return []
    try:
        from googlesearch import search as gsearch
        _dbg("Trying googlesearch-python library")
        items = list(gsearch(query, num_results=num, sleep_interval=1, advanced=True))
        results = []
        for item in items:
            if hasattr(item, "url"):
                results.append({
                    "title": getattr(item, "title", "") or item.url,
                    "href":  item.url,
                    "body":  getattr(item, "description", "") or "",
                })
            else:
                results.append({"title": str(item), "href": str(item), "body": ""})
        _dbg(f"googlesearch-python returned {len(results)} results")
        return results
    except Exception as e:
        _dbg(f"googlesearch-python failed: {e}")
        return []


# ── AI Overview extractor ─────────────────────────────────────────────────────

def _extract_ai_overview(html: str) -> Optional[str]:
    if not html:
        return None
    soup = BeautifulSoup(html, "html.parser")

    for sel in [
        "div[data-attrid='wa:/description']",
        "div[jsname='yEVEwb']", "div[jsname='Cpkphb']",
        "div[data-content-feature='1']",
        "div.LGOjhe", "div.yDYNvb", "div[data-tts='answers']",
        "div.wDYxhc", "div.kno-rdesc span",
        "div.ifM9O", "div.cLjAic", "div.fm06If",
    ]:
        el = soup.select_one(sel)
        if el:
            text = el.get_text(" ", strip=True)
            if len(text) > 60:
                return _clean_ai_text(text)

    for tag in soup.find_all(["h2", "h3", "span", "div"]):
        t = tag.get_text(" ", strip=True).lower()
        if "ai overview" in t or "ai-powered overview" in t:
            parent = tag.find_parent()
            if parent:
                text = parent.get_text(" ", strip=True)
                if len(text) > 80:
                    return _clean_ai_text(text)

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
            if isinstance(data, dict):
                desc = data.get("description") or data.get("abstract")
                if desc and len(desc) > 60:
                    return _clean_ai_text(desc)
        except Exception:
            pass
    return None


def _clean_ai_text(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    for prefix in ["AI Overview", "AI-powered overview", "Overview", "Summary"]:
        if text.lower().startswith(prefix.lower()):
            text = text[len(prefix):].lstrip(" :–—")
    text = re.sub(r"\s*(More about|Learn more|See more|Source[s]?).*$",
                  "", text, flags=re.IGNORECASE)
    return text.strip()


# ── Main search dispatcher ────────────────────────────────────────────────────

def search(query: str, num: int = 20) -> Tuple[List[dict], Optional[str], str]:
    """
    Returns (results, ai_overview_or_None, engine_label).
    Tries 4 backends in order until one succeeds.
    """
    # 1. DuckDuckGo library (fastest, most reliable)
    results = _search_ddgs(query, num)
    if results:
        # Opportunistically grab Google AI overview
        ai_overview = None
        try:
            g_html, _ = fetch(
                f"https://www.google.com/search?q={quote_plus(query)}&num=1&hl=en"
            )
            if g_html and "captcha" not in g_html.lower():
                ai_overview = _extract_ai_overview(g_html)
        except Exception:
            pass
        return results, ai_overview, "DuckDuckGo"

    # 2. DuckDuckGo HTML scrape (no library needed)
    results = _search_ddg_html(query, num)
    if results:
        return results, None, "DuckDuckGo"

    # 3. Google HTML scrape
    results, ai_overview = _search_google_html(query, num)
    if results:
        return results, ai_overview, "Google"

    # 4. googlesearch-python library (last resort)
    results = _search_google_lib(query, num)
    if results:
        return results, None, "Google"

    return [], None, "—"


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
_AI_LINE_PREFIX   = "\x01AI\x01"
_AI_SOURCE_PREFIX = "\x01SRC\x01"

def build_search_page(
    results: List[dict],
    query: str,
    engine_label: str = "DuckDuckGo",
    ai_overview: Optional[str] = None,
    show_ai: bool = True,
) -> Tuple[List[str], List[Tuple[int, str, str]]]:
    lines: List[str] = [
        "",
        f"  {engine_label} Search: {query}",
        "  " + "━" * 60,
        "",
    ]
    links: List[Tuple[int, str, str]] = []

    if show_ai and ai_overview:
        ai_url = f"https://www.google.com/search?q={quote_plus(query)}"
        lines.append(_AI_LINE_PREFIX + "  ✦  AI OVERVIEW")
        lines.append(_AI_LINE_PREFIX + "  " + "─" * 58)
        for para in ai_overview.split("\n"):
            para = para.strip()
            if not para:
                continue
            for wl in textwrap.wrap(para, 86) or [""]:
                lines.append(_AI_LINE_PREFIX + "  " + wl)
        lines.append(_AI_SOURCE_PREFIX + f"  ↗  View on Google: {ai_url}")
        links.append((len(lines) - 1, "View AI Overview on Google", ai_url))
        lines.append(_AI_LINE_PREFIX + "  " + "─" * 58)
        lines.append("")

    for i, r in enumerate(results, 1):
        title = r.get("title", "") or r.get("href", "No title")
        url   = r.get("href",  "") or r.get("url", "")
        body  = r.get("body",  "") or r.get("snippet", "")
        if not url:
            continue
        display_url = url if len(url) <= 72 else url[:69] + "…"
        links.append((len(lines), title, url))
        lines.append(f"  [{i}] {title}")
        lines.append(f"       {display_url}")
        if body:
            for wl in textwrap.wrap(re.sub(r"\s+", " ", body).strip(), 86):
                lines.append(f"       {wl}")
        lines.append("")

    return lines, links


# ── Curses Browser ────────────────────────────────────────────────────────────
class Browser:
    def __init__(self, stdscr: curses.window):
        self.scr    = stdscr
        self.lines: List[str]                                = []
        self.links: List[Tuple[int, str, str]]               = []
        self.history: List[Tuple[str, List[str], list, int]] = []
        self.scroll      = 0
        self.sel_link    = -1
        self.url         = ""
        self.status_msg  = ""
        self.show_ai     = True
        self._last_query   = ""
        self._last_results: List[dict]    = []
        self._last_ai_ov:   Optional[str] = None
        self._last_engine  = "DuckDuckGo"
        self._setup_colors()

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1,  curses.COLOR_WHITE,  curses.COLOR_BLUE)
        curses.init_pair(2,  curses.COLOR_YELLOW, -1)
        curses.init_pair(3,  curses.COLOR_CYAN,   -1)
        curses.init_pair(4,  curses.COLOR_BLACK,  curses.COLOR_GREEN)
        curses.init_pair(5,  curses.COLOR_WHITE,  -1)
        curses.init_pair(6,  curses.COLOR_RED,    -1)
        curses.init_pair(7,  curses.COLOR_BLACK,  curses.COLOR_WHITE)
        curses.init_pair(8,  curses.COLOR_BLACK,  curses.COLOR_CYAN)
        curses.init_pair(9,  curses.COLOR_YELLOW, curses.COLOR_BLUE)
        curses.init_pair(10, curses.COLOR_GREEN,  -1)
        curses.init_pair(11, curses.COLOR_BLACK,  curses.COLOR_YELLOW)
        curses.init_pair(12, curses.COLOR_YELLOW, -1)
        curses.init_pair(13, curses.COLOR_GREEN,  -1)

    def _rows(self): return self.scr.getmaxyx()[0]
    def _cols(self): return self.scr.getmaxyx()[1]
    def _view_h(self): return max(1, self._rows() - 3)

    def _draw_urlbar(self):
        cols        = self._cols()
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
            line = f" {self.status_msg} "[:cols].ljust(cols)
        else:
            total = max(1, len(self.lines))
            pct   = min(100, int(100 * (self.scroll + self._view_h()) / total))
            pos   = f" {self.scroll+1}-{min(self.scroll+self._view_h(), total)}/{total} ({pct}%) "
            back  = f" ← {len(self.history)} " if self.history else ""
            ai_ind = " [AI✦ON] " if self.show_ai else " [AI✦OFF] "
            pad   = max(0, cols - len(pos) - len(back) - len(ai_ind))
            line  = (back + " " * pad + ai_ind + pos)[:cols].ljust(cols)
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
        cols          = self._cols()
        view_h        = self._view_h()
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

            is_ai_hdr = raw_text.startswith(_AI_LINE_PREFIX)
            is_ai_src = raw_text.startswith(_AI_SOURCE_PREFIX)
            if is_ai_hdr:
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
            elif is_ai_hdr:
                attr = (curses.color_pair(11) | curses.A_BOLD
                        if ("✦" in s and "AI" in s.upper())
                        else curses.color_pair(12))
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

    def scroll_by(self, delta: int):
        max_s = max(0, len(self.lines) - self._view_h())
        self.scroll = max(0, min(self.scroll + delta, max_s))

    def scroll_to(self, line: int):
        max_s = max(0, len(self.lines) - self._view_h())
        self.scroll = max(0, min(line, max_s))

    def next_link(self, direction: int = 1):
        if not self.links:
            return
        self.sel_link = (self.sel_link + direction) % len(self.links) if self.sel_link >= 0 else 0
        li = self.links[self.sel_link][0]
        if li < self.scroll or li >= self.scroll + self._view_h():
            self.scroll_to(max(0, li - self._view_h() // 2))

    def _edit_urlbar(self, prefill: str = "") -> str:
        cols    = self._cols()
        prompt  = " ❯ "
        field_w = cols - len(prompt) - 1
        buf     = list(prefill or self.url or "")
        cur     = len(buf)
        curses.curs_set(1)

        def _render():
            start    = max(0, cur - field_w + 1)
            view_str = "".join(buf[start:start + field_w]).ljust(field_w)
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
                    result = "".join(buf).strip(); break
                elif ch == 27:
                    result = ""; break
                elif ch in (curses.KEY_BACKSPACE, 127, 8):
                    if cur > 0:
                        del buf[cur - 1]; cur -= 1
                elif ch == curses.KEY_DC:
                    if cur < len(buf): del buf[cur]
                elif ch == curses.KEY_LEFT:
                    cur = max(0, cur - 1)
                elif ch == curses.KEY_RIGHT:
                    cur = min(len(buf), cur + 1)
                elif ch == curses.KEY_HOME:
                    cur = 0
                elif ch == curses.KEY_END:
                    cur = len(buf)
                elif 32 <= ch <= 126:
                    buf.insert(cur, chr(ch)); cur += 1
        finally:
            curses.curs_set(0)
        return result or ""

    def _loading(self, msg: str):
        cols = self._cols()
        try:
            self.scr.attron(curses.color_pair(8) | curses.A_BOLD)
            self.scr.addstr(1, 0, f"  ⏳ {msg} "[:cols].ljust(cols))
            self.scr.attroff(curses.color_pair(8) | curses.A_BOLD)
            self.scr.refresh()
        except curses.error:
            pass

    def load_url(self, url: str, push_history: bool = True):
        if push_history and self.lines:
            self.history.append((self.url, self.lines[:], self.links[:], self.scroll))
        self._loading(f"Loading {url[:70]}…")
        html, final_url = fetch(url)
        if html is None:
            self.lines = ["", f"  ✗  {final_url}", "", "  Could not load:", f"  {url}"]
            self.links = []
            self.url   = url
        else:
            self.lines, self.links = parse_page(html, final_url)
            self.url = final_url
        self.scroll = 0; self.sel_link = -1; self.status_msg = ""

    def load_search(self, query: str, push_history: bool = True):
        if push_history and self.lines:
            self.history.append((self.url, self.lines[:], self.links[:], self.scroll))
        self._loading(f"Searching: {query[:60]}…")

        results, ai_overview, engine_label = search(query)

        self._last_query   = query
        self._last_results = results
        self._last_ai_ov   = ai_overview
        self._last_engine  = engine_label

        if not results:
            self.lines = [
                "",
                "  ✗  No results found.",
                "",
                "  All search backends failed. Possible causes:",
                "    • No internet connection",
                "    • DuckDuckGo temporarily rate-limiting (wait a moment and retry)",
                "    • Firewall blocking outbound HTTPS",
                "",
                "  Run this outside the browser to diagnose:",
                f"    tbrowse --debug \"{query}\"",
                "",
            ]
            self.links = []
            self.status_msg = "No results — try --debug mode to diagnose"
        else:
            self.lines, self.links = build_search_page(
                results, query, engine_label,
                ai_overview=ai_overview, show_ai=self.show_ai,
            )
            ai_note = " ✦AI" if (ai_overview and self.show_ai) else ""
            self.status_msg = f"{len(results)} results via {engine_label}{ai_note}"

        self.url = f"search: {query}"
        self.scroll = 0; self.sel_link = -1

    def toggle_ai(self):
        self.show_ai = not self.show_ai
        if self._last_query and self._last_results:
            self.lines, self.links = build_search_page(
                self._last_results, self._last_query, self._last_engine,
                ai_overview=self._last_ai_ov, show_ai=self.show_ai,
            )
            ai_note = " ✦AI" if (self._last_ai_ov and self.show_ai) else ""
            self.status_msg = f"{len(self._last_results)} results via {self._last_engine}{ai_note}"
            self.scroll = 0; self.sel_link = -1
        else:
            self.status_msg = f"AI Overview: {'ON' if self.show_ai else 'OFF'}"

    def go_back(self):
        if not self.history:
            self.status_msg = "No history"; return
        self.url, self.lines, self.links, self.scroll = self.history.pop()
        self.sel_link = -1; self.status_msg = ""

    def show_history(self):
        if not self.history:
            self.lines = ["", "  No history yet."]; self.links = []
        else:
            self.lines = ["", "  BROWSING HISTORY", "  " + "━" * 50, ""]
            self.links = []
            for i, (u, _, _, _) in enumerate(reversed(self.history), 1):
                self.links.append((len(self.lines), u, u))
                display = u if len(u) <= 90 else u[:87] + "…"
                self.lines.append(f"  [{i}]  {display}")
                self.lines.append("")
        self.scroll = 0; self.sel_link = -1
        self.url = "history"; self.status_msg = ""

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
                "    Tab / n     →  next link        Enter  →  follow link",
                "    a           →  toggle AI Overview on / off",
                "    b           →  back             h      →  history",
                "    j / k       →  scroll           q      →  quit",
                "",
                "  ✦  AI Overviews appear above search results when available.",
                "  ✦  If search fails, run:  tbrowse --debug \"your query\"",
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
                if 0 <= self.sel_link < len(self.links):
                    self.load_url(self.links[self.sel_link][2])
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
                if self.url.startswith("search:"):
                    self.load_search(self.url[7:].strip(), push_history=False)
                elif self.url:
                    self.load_url(self.url, push_history=False)
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
                    elif bstate & curses.BUTTON1_CLICKED and my == 0:
                        self.open_prompt()
                except curses.error:
                    pass
            elif key == curses.KEY_RESIZE:
                self.scr.clear()


# ── Debug / CLI mode ──────────────────────────────────────────────────────────
def _run_debug(query: str):
    print(f"\n[tbrowse --debug] Searching for: {query!r}\n")
    results, ai_overview, engine = search(query)
    print(f"  Engine used : {engine}")
    print(f"  Results     : {len(results)}")
    print(f"  AI Overview : {ai_overview[:120] + '…' if ai_overview else '(none)'}")
    print()
    for i, r in enumerate(results[:5], 1):
        print(f"  [{i}] {r.get('title','?')}")
        print(f"       {r.get('href','?')}")
        body = r.get("body","") or r.get("snippet","")
        if body:
            print(f"       {body[:100]}")
        print()
    if not results:
        print("  No results returned by any backend.")
        print("  Check your internet connection or try again in a moment.")


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
    if _DEBUG and len(sys.argv) > 1:
        _run_debug(" ".join(sys.argv[1:]))
    else:
        try:
            curses.wrapper(main)
        except KeyboardInterrupt:
            pass
        except Exception as e:
            print(f"\nCrash: {e}")
            traceback.print_exc()
