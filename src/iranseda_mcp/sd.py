"""Iranseda tools: radio channels, live stream and now/next, daily schedule, programs, episodes with audio files."""

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Annotated, Literal

from pydantic import Field

from .http import SITE, ApiError, fetch
from .registry import tool

TEHRAN = timezone(timedelta(hours=3, minutes=30))
KINDS = Literal["radio", "provincial", "nama"]
KIND_PARAM = {"radio": "radio", "provincial": "ostani", "nama": "nama"}
MONTHS = ["فروردین", "اردیبهشت", "خرداد", "تیر", "مرداد", "شهریور", "مهر", "آبان", "آذر", "دی", "بهمن", "اسفند"]
Channel = Annotated[
    str, Field(description="Channel id from sd_channels, e.g. 11 (Radio Iran), 15 (Quran), 26 (Khabar).")
]


def _t(s: str | None) -> str:
    """Tags removed, entities decoded, whitespace collapsed."""
    return re.sub(
        r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<!--.*?-->", "", s or "", flags=re.S)))
    ).strip()


def _first(pattern: str, s: str, flags: int = re.S) -> str:
    m = re.search(pattern, s, flags)
    return _t(m.group(1)) if m else ""


def _norm(s: str) -> str:
    """Fold Arabic letter variants and ZWNJ so a search matches either spelling."""
    return _t(s).translate(str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "\u200c": " "})).lower()


def _channel(channel: str) -> str:
    if not re.fullmatch(r"\d{1,4}", channel.strip()):
        raise ApiError("channel must be a numeric id from sd_channels, e.g. 11.")
    return channel.strip()


def _greg(jy: int, jm: int, jd: int) -> str:
    """Jalali date to Gregorian YYYY-MM-DD."""
    jy -= 979
    jm -= 1
    jd -= 1
    j_days = 365 * jy + jy // 33 * 8 + (jy % 33 + 3) // 4
    j_days += sum([31, 31, 31, 31, 31, 31, 30, 30, 30, 30, 30, 29][:jm]) + jd
    g = j_days + 79
    gy = 1600 + 400 * (g // 146097)
    g %= 146097
    leap = True
    if g >= 36525:
        g -= 1
        gy += 100 * (g // 36524)
        g %= 36524
        if g >= 365:
            g += 1
        else:
            leap = False
    gy += 4 * (g // 1461)
    g %= 1461
    if g >= 366:
        leap = False
        g -= 1
        gy += g // 365
        g %= 365
    gm_days = [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    gm = 0
    while g >= gm_days[gm]:
        g -= gm_days[gm]
        gm += 1
    return f"{gy:04d}-{gm + 1:02d}-{g + 1:02d}"


def _jdate(day: str, month: str, year: str) -> str | None:
    """Gregorian date from the site's Jalali day, month name and year."""
    try:
        return _greg(int(year), MONTHS.index(month.strip()) + 1, int(day))
    except ValueError:
        return None


def _minutes(s: str) -> int | None:
    """Minutes from texts like '45 دقیقه' or '1 ساعت 30 دقیقه'."""
    h = re.search(r"(\d+)\s*ساعت", s)
    m = re.search(r"(\d+)\s*دقیقه", s)
    if not (h or m):
        return int(s) if s.isdigit() else None
    return int(h.group(1) if h else 0) * 60 + int(m.group(1) if m else 0)


def _hm(s: str | None) -> str | None:
    """'0:5' -> '00:05'."""
    m = re.fullmatch(r"(\d{1,2}):(\d{1,2})", s or "")
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}" if m else s


def _day(date: str | None) -> tuple[str, str]:
    """(YYYY-MM-DD, the site's MM/DD/YYYY) for a date, default today in Tehran."""
    if not date:
        d = datetime.now(TEHRAN)
    else:
        try:
            d = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise ApiError("date must look like 2026-11-18 (Gregorian, Tehran day).") from None
    return d.strftime("%Y-%m-%d"), d.strftime("%m/%d/%Y")


def _link(path: str, **q) -> str:
    return f"{SITE}/{path}/?VALID=TRUE&" + "&".join(f"{k}={v}" for k, v in q.items())


@tool("List radio channels")
async def sd_channels(
    kind: Annotated[
        KINDS,
        Field(description="radio = national networks, provincial = provincial radios, nama = Nama (video) radios."),
    ] = "radio",
) -> dict:
    """List IRIB radio channels with the program playing now.

    Use the id with sd_live_now (stream URL), sd_schedule or sd_search_programs.
    """
    page = await fetch(_link("radiolist", t=KIND_PARAM[kind]))
    out = []
    for a in page.split('<article class="col-plus-lg-4')[1:]:
        ch = re.search(r"live/\?VALID=TRUE&(?:amp;)?ch=(\d+)", a)
        if not ch:
            continue
        t = re.search(r"(\d+:\d+)\s*-\s*مدت:\s*(.*)", _first(r'<p class="ott-synopsis[^>]*>(.*?)</p>', a))
        out.append(
            {
                "id": ch.group(1),
                "name": _first(r"<h4[^>]*>(.*?)</h4>", a),
                "now": _first(r"<h3[^>]*>(.*?)</h3>", a) or None,
                "now_start": _hm(t.group(1)) if t else None,
                "now_minutes": _minutes(t.group(2)) if t else None,
                "url": _link("live", ch=ch.group(1)),
            }
        )
    return {"kind": kind, "count": len(out), "channels": out}


def _slot(a: str) -> dict:
    """One program slot from the now/next blocks of a live page."""
    spans = re.findall(r"<span>\s*([^<]*?)\s*</span>", a.split("ott-name", 1)[-1])
    return {
        "program_id": _first(r"[?&]m=(\w+)", a) or None,
        "program": _first(r'<h4 class="ott-name[^>]*>(.*?)</h4>', a),
        "start": _hm(spans[0]) if spans else None,
        "minutes": _minutes(spans[1]) if len(spans) > 1 else None,
    }


@tool("Live stream and now/next")
async def sd_live_now(channel: Channel) -> dict:
    """Get a radio channel's public live stream URL and what is on now and next.

    The stream is an HLS (.m3u8) link that audio players can open. Times are Tehran time.
    """
    ch = _channel(channel)
    page = await fetch(_link("live", ch=ch))
    stream = _first(r'property="og:video:url" content="([^"]*)"', page)
    try:  # the player page carries the https stream; the radio page's own link is often http on port 1935, which fails
        player = await fetch(f"https://player.iranseda.ir/live-player/?VALID=TRUE&CH={ch}")
        stream = _first(r'property="og:video:url" content="([^"]*)"', player) or stream
    except ApiError:
        pass
    if not stream and "currentProgDataContainer" not in page:
        raise ApiError(f"No live page for channel {ch}. Check the id with sd_channels.")
    name = _first(r"<title>.*-\s*(.*?)\s*</title>", page)
    cur, _, nxt = page.partition("currentProgDataContainer")[2].partition("در ادامه خواهید شنید")
    now = _slot(cur)
    next_ = [_slot(a) for a in nxt.split("<article")[1:]]
    return {
        "channel": ch,
        "name": name if name != "شبکه" else None,  # provincial radios have no name on the page
        "stream_url": stream or None,
        "now": now if now["program"] else None,
        "next": next_,
        "url": _link("live", ch=ch),
    }


@tool("Channel schedule for a day")
async def sd_schedule(
    channel: Channel,
    date: Annotated[
        str | None,
        Field(
            description="Gregorian YYYY-MM-DD, default today. Past days show what aired; the site offers about 3 days ahead."
        ),
    ] = None,
) -> dict:
    """List a radio channel's programs for one day, with episode ids.

    Use an episode id with sd_episode to get its audio file.
    """
    ch = _channel(channel)
    day, site_day = _day(date)
    page = await fetch(_link("epglist", ch=ch, d=site_day))
    items = []
    for b in page.split("cd-timeline-block")[1:]:
        e = re.search(r"epgarchivePart/\?[^\"]*?e=(\d+)", b)
        spans = re.findall(r'<span class="fa-number">\s*([^<]*?)\s*</span>', b)
        if not e:
            continue  # the first block is the day header
        items.append(
            {
                "start": _hm(spans[0]) if spans else None,
                "minutes": _minutes(spans[-1]) if len(spans) > 1 else None,
                "title": _first(r'<h4 class="ott-sub-name[^>]*>(.*?)</h4>', b),
                "program_id": _first(r"[?&]m=(\w+)", b) or None,
                "episode_id": e.group(1) if e else None,
            }
        )
    return {
        "channel": ch,
        "date": day,
        "site_date": _first(r'<div class="col-plus divheader[^"]*">\s*<span[^>]*>(.*?)</span>', page),
        "count": len(items),
        "programs": items,
    }


@tool("Search radio programs")
async def sd_search_programs(
    channel: Channel,
    query: Annotated[
        str | None, Field(description="Part of the Persian program name; empty lists every program of the channel.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict:
    """Find programs of one radio channel by name (the site has no global search).

    Use program_id with sd_program for details and episodes.
    """
    ch = _channel(channel)
    page = await fetch(_link("programlist", ch=ch))
    q = _norm(query or "")
    out = []
    for a in page.split('<article class="col-plus-lg-3')[1:]:
        m = re.search(r"Program/\?[^\"]*?m=(\w+)", a, re.I)
        if not m:
            continue
        name = _first(r'<h4 class="ott-name[^>]*>(.*?)</h4>', a)
        if q and q not in _norm(name):
            continue
        out.append(
            {
                "program_id": m.group(1),
                "name": name,
                "ended_jalali": _first(r"تاریخ اتمام:\s*([\d/]+)", a)
                or None,  # the site lists some airing shows as ended
                "url": _link("program", ch=ch, m=m.group(1)),
            }
        )
    return {"channel": ch, "query": query, "total": len(out), "programs": out[:limit]}


@tool("Program details and episodes")
async def sd_program(
    channel: Channel,
    program_id: Annotated[str, Field(description="Program id from sd_search_programs or sd_schedule, e.g. 015137.")],
    page: Annotated[int, Field(ge=1, description="Episode page, newest first, about 20 per page.")] = 1,
) -> dict:
    """Get a radio program's description, broadcast time and its latest episodes.

    Use episode_id with sd_episode to get audio files.
    """
    ch = _channel(channel)
    if not re.fullmatch(r"\w{1,12}", program_id):
        raise ApiError("program_id looks like 015137; take it from sd_search_programs.")
    html_ = await fetch(f"{SITE}/program/?ch={ch}&m={program_id}&pn={page}")
    name = _first(r'<h1[^>]*class="prog-name[^>]*>(.*?)</h1>', html_)
    if not name:
        raise ApiError("Program not found. Check channel and program_id with sd_search_programs.")
    desc = " ".join(_t(p) for p in re.findall(r'<p class="color-white[^>]*>(.*?)</p>', html_, re.S) if _t(p))
    episodes = []
    for li in re.findall(r"<li>\s*<a class=\"ch-text-color[^>]*epgarchivePart.*?</li>", html_, re.S):
        e = re.search(r"[?&]e=(\d+)", li)
        day = _first(r"<h2>(.*?)</h2>", li)
        month = _first(r"<h4>\s*(\S+)", li)
        ym = re.search(r"(\d{4}) \(ساعت (\d+:\d+)\)", _t(li))
        year, tm = ym.groups() if ym else (None, None)
        g = _jdate(day, month, year) if year else None
        episodes.append(
            {
                "episode_id": e.group(1) if e else None,
                "date": g,
                "time": _hm(tm),
                "jalali": f"{day} {month} {year}" if year else None,
                "note": _first(r'<div class="col-sm-10 col-xs-6[^>]*>(.*?)</div>', li) or None,
            }
        )
    pages = [int(n) for n in re.findall(r"[?&]pn=(\d+)", html_)]
    return {
        "channel": ch,
        "program_id": program_id,
        "name": name,
        "schedule": _first(r'<h2 class="guide-prog[^>]*>(.*?)</h2>', html_) or None,
        "description": desc or None,
        "page": page,
        "last_page": max([page, *pages]),
        "episodes": episodes,
        "url": _link("program", ch=ch, m=program_id),
    }


@tool("Episode and audio files")
async def sd_episode(
    channel: Channel,
    episode_id: Annotated[str, Field(description="Numeric episode id from sd_schedule or sd_program.")],
) -> dict:
    """Get one episode's date, description and the audio file URL of each part.

    mp3_url is a direct download of the recording (large file); nothing is downloaded here.
    """
    ch = _channel(channel)
    if not re.fullmatch(r"\d{3,12}", episode_id):
        raise ApiError("episode_id is a number such as 154174106; take it from sd_schedule or sd_program.")
    page = await fetch(_link("epgarchivePart", ch=ch, e=episode_id))
    title = _first(r'<h1[^>]*class="prog-name[^>]*>(.*?)</h1>', page)
    if not title:
        raise ApiError("Episode not found. Check channel and episode_id.")
    when = re.search(r"(\d+) (\S+) (\d{4})", _first(r'<h2 class="guide-prog[^>]*>(.*?)</h2>', page))
    head = _first(r'<h2 class="guide-prog[^>]*>(.*?)</h2>', page)
    modals = {
        mid: re.findall(r'href="([^"]*DLFile[^"]*)"[^>]*>.*?\((\.\w+)\)', body, re.S)
        for mid, body in re.findall(r'id="(myModal\d+)"(.*?)(?=id="myModal|</body>)', page, re.S)
    }
    parts = []
    for a in page.split("<article")[1:]:
        mm = re.search(r'href="#(myModal\d+)"', a)
        if not mm:
            continue
        t = re.search(r"start_time[^>]*>\s*([\d:]+)\s*</span><span[^>]*end_time[^>]*>\s*([\d:]+)", a)
        files = {ext.lstrip("."): url for url, ext in modals.get(mm.group(1), [])}
        parts.append(
            {
                "start": t.group(1) if t else None,
                "end": t.group(2) if t else None,
                "mp3_url": files.get("mp3"),
                "mp4_url": files.get("mp4"),
            }
        )
    info = page.split("اطلاعات برنامه", 1)[-1].split("فهرست قطعات", 1)[0]
    return {
        "channel": ch,
        "episode_id": episode_id,
        "program": title,
        "date": _jdate(*when.groups()) if when else None,
        "when": head,
        "note": _first(r'<p class="teaser-text[^>]*>(.*?)</p>', info) or None,
        "description": _first(r"<p[^>]*>\s*([^<]{30,}?)\s*</p>", page.split("فهرست قطعات", 1)[-1]) or None,
        "parts": parts,
        "url": _link("epgarchivePart", ch=ch, e=episode_id),
    }
