"""Tables, text and links from saved HTML pages, with the standard library only.

``parse_html(text)`` builds a light DOM with ``html.parser`` and returns an
``HtmlPage``: the ``<title>``, the visible text, every ``<table>`` in document
order and every link. RBI's pages and the HTML inside its RSS feed are the
main users (``rbi_releases``, ``sovereign``), but nothing here is RBI-specific
except ``check_not_blocked``.

Each ``Table`` keeps only its **direct** rows (``<tr>`` under the table or its
``<thead>``, ``<tbody>``, ``<tfoot>``), so the rows of a nested table never
leak into the table around it. A cell's ``text`` does include the text of any
table nested inside it, as a browser shows it. ``Table.grid()`` expands
``colspan`` and ``rowspan`` into a rectangular grid, repeating a spanning
cell's text in every position it covers (``fill=False`` leaves them empty).
``depth`` counts enclosing tables (1 for a top-level table); ``is_leaf`` is
true for a table with no table inside it. RBI's release pages and RSS items
nest their data tables at different depths, so pick data tables by content,
not by depth or class.

Text is normalised once: ``&nbsp;`` (U+00A0), zero-width spaces and runs of
whitespace become one space, ``<br>`` and block elements (``p``, ``div``,
``td``...) separate words, and ``script``/``style`` are dropped. Comments are
never text, so links commented out of a page are not returned.

The builder copes with the usual hand-written HTML: a ``<td>``, ``<tr>`` or
``<tbody>`` start closes the open cell or row of the same table (never one of
an enclosing table), ``<p>`` closes an open ``<p>``, and an end tag with no
matching open element inside the current cell is ignored.

Cell values: ``parse_number`` reads ``73,016.600``, ``1,01,908.561`` (Indian
grouping), ``18.3591%``, ``₹6,000`` and ``NIL`` (zero); blank, ``-``, ``NA``
are ``None``; anything else raises ``ValueError``, so a garbled cell is never
read as a number. ``parse_count`` does the same for whole numbers and
``parse_date`` tries the formats it is given, after dropping a weekday in
parentheses (``November 20, 2026 (Friday)``).
"""

from __future__ import annotations

import datetime as dt
import re
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path

VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
})
_BLOCK_TAGS = frozenset({
    "address", "article", "aside", "blockquote", "br", "caption", "center", "dd", "div", "dl", "dt",
    "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header", "hr", "li", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "td", "tfoot", "th", "thead", "tr", "ul",
})
_SKIP_TAGS = frozenset({"script", "style", "noscript", "template", "title"})
_SECTION_TAGS = frozenset({"thead", "tbody", "tfoot"})
_CELL_TAGS = frozenset({"td", "th"})
_P_CLOSERS = frozenset({
    "p", "div", "table", "ul", "ol", "dl", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote",
    "center", "hr", "section", "article", "header", "footer", "nav", "address",
})
_MAX_SPAN = 1000
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
NULL_MARKERS = frozenset({"", "-", "--", "–", "—", "na", "n.a.", "n.a", "n/a", "#error"})
ZERO_MARKERS = frozenset({"nil"})


def clean_text(text: str) -> str:
    """``text`` with NBSP and zero-width characters removed or spaced, and whitespace collapsed."""
    text = str(text).replace("\xa0", " ").translate(_INVISIBLE)
    return re.sub(r"\s+", " ", text).strip()


class Element:
    """One element of the DOM: ``tag``, ``attrs`` (lower-case names), ``children`` and ``parent``."""

    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: Element | None):
        self.tag = tag
        self.attrs = attrs
        self.children: list[Element | str] = []
        self.parent = parent

    def __repr__(self) -> str:
        return f"<{self.tag} {self.attrs}>" if self.attrs else f"<{self.tag}>"

    def iter(self, tag: str | None = None) -> Iterator[Element]:
        """Descendants in document order (not ``self``), optionally only those named ``tag``."""
        stack = list(reversed([c for c in self.children if isinstance(c, Element)]))
        while stack:
            node = stack.pop()
            if tag is None or node.tag == tag:
                yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Element)]))

    def ancestors(self) -> Iterator[Element]:
        node = self.parent
        while node is not None:
            yield node
            node = node.parent

    def text(self, exclude: Iterable[Element] = ()) -> str:
        """Visible text under this element, leaving out the subtrees in ``exclude``."""
        skip = {id(e) for e in exclude}
        parts: list[str] = []
        stack: list[Element | str | None] = list(reversed(self.children))
        while stack:
            item = stack.pop()
            if item is None:  # end of a block element
                parts.append(" ")
            elif isinstance(item, str):
                parts.append(item)
            elif item.tag in _SKIP_TAGS:
                continue
            elif id(item) in skip:
                parts.append(" ")
            else:
                if item.tag in _BLOCK_TAGS:
                    parts.append(" ")
                    stack.append(None)
                stack.extend(reversed(item.children))
        return clean_text("".join(parts))


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Element("#document", {}, None)
        self.cur = self.root

    def _open_below(self, stop: frozenset[str]):
        """Open elements from the current one up to (not including) the nearest ``stop`` tag."""
        node = self.cur
        while node is not self.root and node.tag not in stop:
            yield node
            node = node.parent

    def _close_outermost(self, tags: frozenset[str], stop: frozenset[str]) -> None:
        outermost = None
        for node in self._open_below(stop):
            if node.tag in tags:
                outermost = node
        if outermost is not None:
            self.cur = outermost.parent

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _CELL_TAGS:
            self._close_outermost(_CELL_TAGS, frozenset({"table"}))
        elif tag == "tr":
            self._close_outermost(_CELL_TAGS | {"tr"}, frozenset({"table"}))
        elif tag in _SECTION_TAGS:
            self._close_outermost(_CELL_TAGS | _SECTION_TAGS | {"tr"}, frozenset({"table"}))
        elif tag == "li":
            self._close_outermost(frozenset({"li"}), frozenset({"ul", "ol", "table", "td", "th"}))
        elif tag == "body":
            self._close_outermost(frozenset({"head"}), frozenset())
        if tag in _P_CLOSERS:
            self._close_outermost(frozenset({"p"}), frozenset({"table", "td", "th", "li"}))
        node = Element(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.children.append(node)
        if tag not in VOID_TAGS:
            self.cur = node

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in VOID_TAGS:
            self.cur.children.append(Element(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur))
        else:  # <td/> and the like: an empty element
            self.handle_starttag(tag, attrs)
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in VOID_TAGS:
            return
        if tag == "table" or tag in _CELL_TAGS or tag in _SECTION_TAGS or tag == "tr":
            stop = frozenset({"table"}) if tag != "table" else frozenset()
        else:
            stop = frozenset({"table", "td", "th"})
        for node in self._open_below(stop):
            if node.tag == tag:
                self.cur = node.parent
                return

    def handle_data(self, data: str) -> None:
        self.cur.children.append(data)


@dataclass(frozen=True)
class Cell:
    """A table cell as printed: its text, spans, whether it is a ``<th>``, and its links."""

    text: str
    colspan: int = 1
    rowspan: int = 1
    header: bool = False
    links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, eq=False)
class Table:
    """A ``<table>``: ``rows`` are its direct rows only, ``text`` all text inside it (nested tables too)."""

    index: int
    depth: int
    parent: int | None
    children: tuple[int, ...]
    attrs: dict[str, str]
    rows: tuple[tuple[Cell, ...], ...]
    text: str
    element: Element = field(repr=False)

    @property
    def is_leaf(self) -> bool:
        """True if no table is nested inside this one."""
        return not self.children

    def cell_texts(self) -> list[list[str]]:
        """Each direct row as the list of its cells' text, without expanding spans."""
        return [[c.text for c in row] for row in self.rows]

    def grid(self, fill: bool = True) -> list[list[str]]:
        """Rows with ``colspan`` and ``rowspan`` expanded, all padded to the same width.

        A spanning cell's text is repeated in every row and column it covers
        (``fill=False``: only its top-left position holds the text).
        """
        spans: dict[int, list] = {}  # column -> [rows still covered, text]
        out_rows: list[list[str]] = []
        for row in self.rows:
            out: list[str] = []
            col = _take_spans(spans, out, 0, fill)
            for cell in row:
                col = _take_spans(spans, out, col, fill)
                for k in range(cell.colspan):
                    out.append(cell.text if (k == 0 or fill) else "")
                    if cell.rowspan > 1:
                        spans[col] = [cell.rowspan - 1, cell.text if fill else ""]
                    col += 1
            while spans and max(spans) >= col:
                if col in spans:
                    col = _take_spans(spans, out, col, fill)
                else:
                    out.append("")
                    col += 1
            out_rows.append(out)
        width = max((len(r) for r in out_rows), default=0)
        return [r + [""] * (width - len(r)) for r in out_rows]


def _take_spans(spans: dict[int, list], out: list[str], col: int, fill: bool) -> int:
    """Fill ``out`` from column ``col`` with cells spanning down from rows above; the next free column."""
    while col in spans:
        left, text = spans[col]
        out.append(text if fill else "")
        if left <= 1:
            del spans[col]
        else:
            spans[col][0] = left - 1
        col += 1
    return col


@dataclass(frozen=True, eq=False)
class HtmlPage:
    """A parsed page: ``title``, visible ``text``, ``tables`` in document order, ``links`` and the DOM."""

    title: str
    text: str
    tables: tuple[Table, ...]
    links: tuple[tuple[str, str], ...]
    root: Element = field(repr=False)

    def leaf_tables(self, within: Table | None = None) -> list[Table]:
        """Tables with no table inside them, optionally only those nested in ``within``."""
        pool = self.tables if within is None else self.descendants(within)
        return [t for t in pool if t.is_leaf]

    def descendants(self, table: Table) -> list[Table]:
        """Tables nested (at any depth) inside ``table``, in document order."""
        out, todo = [], list(table.children)
        while todo:
            i = todo.pop(0)
            out.append(self.tables[i])
            todo[:0] = list(self.tables[i].children)
        return sorted(out, key=lambda t: t.index)


def _span(value: str | None) -> int:
    m = re.match(r"\s*(\d+)", value or "")
    n = int(m.group(1)) if m else 1
    return min(max(n, 1), _MAX_SPAN)


def _links(node: Element) -> tuple[tuple[str, str], ...]:
    return tuple((a.text(), a.attrs.get("href", "")) for a in node.iter("a") if a.attrs.get("href"))


def _rows(table: Element) -> list[Element]:
    rows = []
    for child in table.children:
        if not isinstance(child, Element):
            continue
        if child.tag == "tr":
            rows.append(child)
        elif child.tag in _SECTION_TAGS:
            rows.extend(c for c in child.children if isinstance(c, Element) and c.tag == "tr")
    return rows


def parse_html(html: str | bytes) -> HtmlPage:
    """Parse an HTML document or fragment (text, or bytes decoded as UTF-8) into an ``HtmlPage``."""
    if isinstance(html, bytes):
        html = html.decode("utf-8-sig", errors="replace")
    builder = _TreeBuilder()
    builder.feed(html)
    builder.close()
    root = builder.root
    title_el = next(root.iter("title"), None)
    title = title_el.text() if title_el is not None else ""
    nodes = list(root.iter("table"))
    position = {id(n): i for i, n in enumerate(nodes)}
    parents: list[int | None] = []
    depths: list[int] = []
    for node in nodes:
        enclosing = [a for a in node.ancestors() if a.tag == "table"]
        parents.append(position[id(enclosing[0])] if enclosing else None)
        depths.append(len(enclosing) + 1)
    children: dict[int, list[int]] = {}
    for i, p in enumerate(parents):
        if p is not None:
            children.setdefault(p, []).append(i)
    tables = []
    for i, node in enumerate(nodes):
        rows = tuple(
            tuple(
                Cell(c.text(), _span(c.attrs.get("colspan")), _span(c.attrs.get("rowspan")), c.tag == "th",
                     _links(c))
                for c in tr.children if isinstance(c, Element) and c.tag in _CELL_TAGS
            )
            for tr in _rows(node)
        )
        tables.append(Table(i, depths[i], parents[i], tuple(children.get(i, ())), dict(node.attrs), rows,
                            node.text(), node))
    return HtmlPage(title, root.text(), tuple(tables), _links(root), root)


def read_text(source: str | Path | bytes) -> tuple[str, str]:
    """``(text, name)`` of a source given as a path, as markup text or as bytes.

    A string that contains ``<`` is taken as markup; any other string, or a
    ``Path``, is read as a UTF-8 file (a byte-order mark is dropped).
    """
    if isinstance(source, bytes):
        return source.decode("utf-8-sig", errors="replace"), "<bytes>"
    if isinstance(source, str) and "<" in source:
        return source, "<text>"
    path = Path(source)
    return path.read_bytes().decode("utf-8-sig", errors="replace"), path.name


class BlockedPageError(ValueError):
    """A saved page is a web firewall's refusal ("Unauthorised Access ... Support ID"), not data."""

    def __init__(self, message: str, support_id: str | None = None):
        super().__init__(message)
        self.support_id = support_id


_BLOCK_TITLE = re.compile(r"unauthori[sz]ed access|request rejected|access denied", re.I)
_BLOCK_TEXT = re.compile(
    r"not authori[sz]ed to view this page|the requested url was rejected|support id", re.I)


_CHALLENGE_TEXT = re.compile(
    r"enable javascript|javascript (?:is )?(?:required|disabled|must be enabled)|checking your browser|"
    r"verify(?:ing)? (?:that )?you are (?:a )?human|are you a robot|captcha", re.I)
_CHALLENGE_MAX_TEXT = 300  # a challenge page shows next to no text; a release page shows menus and footers


def check_not_blocked(page: HtmlPage, name: str = "page") -> None:
    """Raise ``BlockedPageError`` if ``page`` is a refusal rather than content.

    Two kinds are recognised: a firewall page ("Unauthorised Access ...
    Support ID", "Request Rejected"), and a bot challenge: a page with a
    script, no table and at most a line of text that is empty or asks for
    JavaScript (as rbidocs.rbi.org.in serves with HTTP 200).
    """
    if _BLOCK_TITLE.search(page.title) or (not page.tables and _BLOCK_TEXT.search(page.text)):
        m = re.search(r"support id\s*(?:is)?\s*:?\s*(\d+)", page.text, re.I)
        support = m.group(1) if m else None
        detail = f" (Support ID {support})" if support else ""
        raise BlockedPageError(f"{name}: the site refused the request{detail}: "
                               f"{page.title or page.text[:80]!r}; no data in it", support)
    if not page.tables and len(page.text) <= _CHALLENGE_MAX_TEXT and next(page.root.iter("script"), None):
        hidden = clean_text(" ".join(e.text() for e in page.root.iter("noscript")))
        if not page.text or _CHALLENGE_TEXT.search(f"{page.title} {page.text} {hidden}"):
            shown = page.text or hidden or page.title
            raise BlockedPageError(f"{name}: a script-only page in place of content (a bot challenge): "
                                   f"{shown[:80]!r}; no data in it")


# A whole part: plain digits, or grouped in thousands (12,891,490) or the Indian way (1,01,908 and 12,34,567).
_WHOLE = r"(?:\d+|\d{1,3}(?:,\d{3})+|\d{1,2}(?:,\d{2})*,\d{3})"
_NUMBER_BODY = re.compile(r"[+-]?(?:" + _WHOLE + r"(?:\.\d*)?|\.\d+)")


def _strip_number(text: str) -> str:
    """The number part of a cell: a leading ``₹``, ``Rs.`` or ``INR`` and a trailing ``%`` taken off."""
    text = clean_text(text)
    text = re.sub(r"^(?:₹|rs\.?|inr)\s*", "", text, flags=re.I)
    return re.sub(r"\s*%$", "", text)


def parse_number(text: object) -> float | None:
    """A number printed in a table cell; ``None`` for a blank or null marker; ``ValueError`` otherwise.

    Thousands separators in Western or Indian grouping, a leading ``₹`` or
    ``Rs.`` and a trailing ``%`` are allowed; ``NIL`` is zero. Anything else
    is refused: a space inside the digits (two numbers in one cell, ``100
    200``) or commas out of place (``1,2,3``) never read as one number.
    """
    if text is None:
        return None
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    raw = clean_text(str(text))
    low = raw.lower()
    if low in NULL_MARKERS:
        return None
    if low in ZERO_MARKERS:
        return 0.0
    body = _strip_number(raw)
    if not _NUMBER_BODY.fullmatch(body):
        raise ValueError(f"not a number: {raw!r}")
    return float(body.replace(",", ""))


def parse_count(text: object) -> int | None:
    """A whole number from a cell (``'349'``, ``'1,455'``); ``None`` for a blank or null marker."""
    value = parse_number(text)
    if value is None:
        return None
    if value != int(value) or value < 0:
        raise ValueError(f"not a count: {text!r}")
    return int(value)


def parse_date(text: object, formats: Sequence[str]) -> dt.date | None:
    """A date from a cell or phrase, trying ``formats`` in order; ``None`` for a blank cell.

    A weekday in parentheses (``(Friday)``) and a trailing full stop are
    dropped first. Raises ``ValueError`` when no format fits.
    """
    if text is None:
        return None
    raw = clean_text(str(text))
    body = re.sub(r"\s*\((?:mon|tues|wednes|thurs|fri|satur|sun)day\)\s*$", "", raw, flags=re.I).rstrip(".")
    body = clean_text(body)
    if body.lower() in NULL_MARKERS:
        return None
    for fmt in formats:
        try:
            return dt.datetime.strptime(body, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"not a date in {list(formats)}: {raw!r}")


def squash(text: str) -> str:
    """Lower-case letters and digits only: a label key that ignores spacing, hyphens and units' symbols."""
    return re.sub(r"[^a-z0-9]", "", clean_text(text).lower())
