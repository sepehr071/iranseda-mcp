"""Iranseda library tools: audiobooks, music, kids, speeches, nava and Quran recitations with downloadable audio."""

import re
from typing import Annotated, Literal

from pydantic import Field

from .http import ApiError, fetch
from .registry import tool
from .sd import _first, _minutes, _t

SITES = Literal["book", "music", "kids", "sokhanrani", "nava", "quran"]
SiteArg = Annotated[
    SITES,
    Field(
        description="book = audiobooks, music = songs, kids = children, sokhanrani = speeches/lectures, "
        "nava = religious eulogies and prayers, quran = recitations."
    ),
]
_LETTERS = str.maketrans({"ي": "ی", "ك": "ک"})


def _base(site: str) -> str:
    return f"https://{site}.iranseda.ir"


def _clean_query(q: str) -> str:
    return re.sub(r"\s+", " ", q.translate(_LETTERS).replace("\u200c", " ")).strip()


def _item_id(value: str) -> str:
    if not re.fullmatch(r"\d{1,9}", value.strip()):
        raise ApiError("item_id is a number such as 530153; take it from sd_library_search.")
    return value.strip()


@tool("Search audio library")
async def sd_library_search(
    site: SiteArg,
    query: Annotated[
        str | None, Field(description="Persian words to search in titles and people, e.g. نهج البلاغه.")
    ] = None,
    category: Annotated[
        str | None,
        Field(
            description="Category code instead of a query: quran ga; music aaa, ac; nava b; kids ja; sokhanrani e; book daa."
        ),
    ] = None,
    page: Annotated[int, Field(ge=1, description="Result page, 20 to 60 items each.")] = 1,
) -> dict:
    """Search Iranseda's audiobooks, music, kids, speeches, nava and Quran recitations.

    Give a query or a category code. Use item id with sd_library_item. The quran site has no working text search, so
    use category codes there.
    """
    if not (query and query.strip()) and not category:
        raise ApiError("Give a query or a category code.")
    if category:
        if not re.fullmatch(r"[a-z]{1,6}", category.strip().lower()):
            raise ApiError("category is a short letter code such as ga or da.")
        url = f"{_base(site)}/categorylist/"
        params = {"VALID": "TRUE", "c": category.strip().lower(), "pn": str(page)}
    else:
        if site == "quran":
            raise ApiError("The Quran site's search is broken upstream. Use a category code such as ga instead.")
        url = f"{_base(site)}/search/"
        params = {"VALID": "TRUE", "t": _clean_query(query or ""), "tag": "", "pn": str(page)}
    html_ = await fetch(url, params)
    items = []
    for a in re.split(r"<article|<li class=\"col-sm-3", html_)[1:]:
        m = re.search(r"[Dd]etails(?:album|Album)?/\?[^\"']*?g=(\d+)", a)
        if not m:
            continue
        title = (
            _first(r"<h4[^>]*teaser-title[^>]*>(.*?)</h4>", a)
            or _first(r"<h3>(.*?)</h3>", a)
            or _first(r'alt="([^"]*)"', a)
        )
        who = _first(r'<span class="time">(.*?)</span>', a)
        dur = _first(r'<span class="class">(.*?)</span>', a)
        items.append(
            {
                "item_id": m.group(1),
                "title": title,
                "by": who or None,
                "minutes": _minutes(dur) if dur else None,
                "teaser": (_first(r'<p class="teaser-text[^>]*>(.*?)</p>', a) or _first(r"<h3>.*?<p>(.*?)</p>", a))
                or None,
            }
        )
    return {
        "site": site,
        "query": query,
        "category": category,
        "page": page,
        "has_next": f"pn={page + 1}" in html_,
        "count": len(items),
        "items": items,
    }


@tool("Library item and tracks")
async def sd_library_item(site: SiteArg, item_id: str) -> dict:
    """Get an audiobook, song, speech, nava or recitation's details and its tracks.

    Each track has an attachment id; use it with sd_library_file to get the mp3 link.
    """
    gid = _item_id(item_id)
    page = await fetch(f"{_base(site)}/detailsalbum/", {"VALID": "TRUE", "g": gid})
    title = _first(r'<h1[^>]*itemprop="name"[^>]*>(.*?)(?:<span class="subtitle">|</h1>)', page)
    if not title:
        raise ApiError("Item not found. Check site and item_id with sd_library_search.")
    sub = _first(r'<h1[^>]*>.*?<span class="subtitle">(.*?)</span>', page)
    fields = {}
    for f in re.findall(r'<dd class="field">(.*?)</dd>', page, re.S):
        label = _first(r"<strong>(.*?)</strong>", f).rstrip(":： ").strip()
        value = _t(re.sub(r"<strong>.*?</strong>", "", f, flags=re.S))
        if label and value:
            fields.setdefault(label, []).append(value)
    about = page.split('id="about"', 1)[-1].split('id="chapters"', 1)[0]
    body = re.search(
        r'<div class="body-module"[^>]*>(.*?)(?:<ul|<table|<div class="footer-module"|</section>)', about, re.S
    )
    desc = _t(body.group(1)) if body else ""
    tracks = []
    for li in re.findall(r"<li[^>]*>\s*<div class=\"album-detail.*?</li>", page, re.S):
        a = re.search(r"download\?attid=(\d+)", li)
        if not a:
            continue
        d = re.search(r"\b(\d+(?::\d+)+)\b", _first(r'<div class="song-duration[^>]*text-left[^>]*>(.*?)</div>', li))
        tracks.append(
            {
                "attachment_id": a.group(1),
                "title": _first(r"<span>(.*?)</span>", li),
                "length": d.group(1) if d else None,
            }
        )
    info = _first(r'<div class="Info-bar[^>]*>(.*?)</ul>', page)
    return {
        "site": site,
        "item_id": gid,
        "title": title,
        "subtitle": sub or None,
        "details": {k: v[0] if len(v) == 1 else v for k, v in fields.items()},
        "description": desc[:700] or None,
        "stats": info or None,
        "tracks": tracks,
        "url": f"{_base(site)}/detailsalbum/?VALID=TRUE&g={gid}",
    }


@tool("Library track file links")
async def sd_library_file(site: SiteArg, attachment_id: str) -> dict:
    """Get the mp3 and mp4 download links and sizes of one library track.

    Links are returned, never downloaded. Use attachment_id from sd_library_item.
    """
    if not re.fullmatch(r"\d{1,9}", attachment_id.strip()):
        raise ApiError("attachment_id is a number from sd_library_item.")
    aid = attachment_id.strip()
    page = await fetch(f"{_base(site)}/download/", {"attid": aid})
    files = {}
    for url, kind, size in re.findall(
        r'href="([^"]*downloadfile[^"]*)".*?(mp\d)\s*[^<]*?:\s*([\d.]+\s*\w+)', page, re.S
    ):
        files[kind] = {"url": url.replace("&amp;", "&"), "size": size}
    if not files:
        raise ApiError("No file for that attachment id. Take it from sd_library_item of the same site.")
    return {"site": site, "attachment_id": aid, **files}
