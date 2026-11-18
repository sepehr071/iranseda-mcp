import pytest
from conftest import page

pytestmark = pytest.mark.anyio

TOOLS = {
    "sd_channels",
    "sd_live_now",
    "sd_schedule",
    "sd_search_programs",
    "sd_program",
    "sd_episode",
    "sd_library_search",
    "sd_library_item",
    "sd_library_file",
}


async def call(client, name, args):
    return (await client.call_tool(name, args)).structured_content


async def test_all_tools_are_read_only_and_described(client):
    tools = (await client.list_tools()).tools
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.annotations.read_only_hint is True, t.name
        assert t.title and t.description.splitlines()[0].endswith("."), t.name


async def test_channels(client, api):
    api["/radiolist/"] = page("radiolist.html")
    r = await call(client, "sd_channels", {})
    assert r["count"] == 23
    c = r["channels"][0]
    assert c["id"] == "26" and c["name"] == "خبر" and c["now_start"] == "00:00"
    assert api.calls[0].url.params["t"] == "radio"


async def test_live_now(client, api):
    api["/live/"] = page("live.html")
    r = await call(client, "sd_live_now", {"channel": "11"})
    assert r["stream_url"].endswith("radio-iran/playlist.m3u8")
    assert r["now"]["program_id"] and r["now"]["start"] and r["next"][0]["start"]
    assert api.calls[0].url.params["ch"] == "11"


async def test_schedule_skips_day_header(client, api):
    api["/epglist/"] = page("epglist.html")
    r = await call(client, "sd_schedule", {"channel": "11", "date": "2026-11-17"})
    assert api.calls[0].url.params["d"] == "11/17/2026"
    assert r["site_date"].endswith("1405") and r["count"] == 8
    p = r["programs"][0]
    assert p["start"] == "00:00" and p["minutes"] == 30 and p["episode_id"] == "154174133"


async def test_search_programs(client, api):
    api["/programlist/"] = page("programlist.html")
    r = await call(client, "sd_search_programs", {"channel": "11", "query": "آیه بهار"})
    assert [p["program_id"] for p in r["programs"]] == ["016503"]
    assert r["programs"][0]["ended_jalali"] == "1403/12/21"
    r = await call(client, "sd_search_programs", {"channel": "11", "limit": 5})
    assert len(r["programs"]) == 5 and r["total"] > 5


async def test_program_episodes_with_gregorian_dates(client, api):
    api["/program/"] = page("program.html")
    r = await call(client, "sd_program", {"channel": "11", "program_id": "015137"})
    assert r["name"] == "به افق آفتاب" and r["last_page"] > 100 and r["description"]
    e = r["episodes"][0]
    assert (e["episode_id"], e["date"], e["time"], e["jalali"]) == ("154174106", "2026-11-17", "11:30", "26 آبان 1405")
    assert api.calls[0].url.params["pn"] == "1"


async def test_episode_parts(client, api):
    api["/epgarchivePart/"] = page("episode.html")
    r = await call(client, "sd_episode", {"channel": "11", "episode_id": "154174106"})
    assert r["program"] == "به افق آفتاب" and r["date"] == "2026-11-17" and "50 دقیقه" in r["when"]
    assert [(p["start"], p["end"]) for p in r["parts"]] == [("11:30", "12:00"), ("12:00", "12:30")]
    assert r["parts"][0]["mp3_url"].endswith("vid=3241118_227") and r["parts"][0]["mp4_url"]


async def test_bad_input(client, api):
    for name, args in [
        ("sd_live_now", {"channel": "abc"}),
        ("sd_schedule", {"channel": "11", "date": "18/11/2026"}),
        ("sd_episode", {"channel": "11", "episode_id": "x"}),
    ]:
        r = await client.call_tool(name, args)
        assert r.is_error, name
    assert not api.calls


async def test_unknown_program_is_an_error(client, api):
    api["/program/"] = lambda request: __import__("httpx").Response(200, text="<html><body>x</body></html>")
    r = await client.call_tool("sd_program", {"channel": "11", "program_id": "999999"})
    assert r.is_error


async def test_live_prefers_player_https_stream(client, api):
    api["/live/"] = page("live.html")
    api["/live-player/"] = page("player.html")
    r = await call(client, "sd_live_now", {"channel": "11"})
    assert r["stream_url"] == "https://s1cdn3.iranseda.ir/liveedge/radio-iran/playlist.m3u8"


async def test_library_search_query(client, api):
    api["/search/"] = page("lib_search.html")
    r = await call(client, "sd_library_search", {"site": "book", "query": "اندیشه"})
    assert api.calls[0].url.host == "book.iranseda.ir" and api.calls[0].url.params["pn"] == "1"
    assert r["count"] == 3 and r["has_next"] and r["items"][0]["item_id"] == "530153"
    assert r["items"][0]["title"].startswith("طرح")


async def test_library_category_list_layout(client, api):
    api["/categorylist/"] = page("lib_music_category.html")
    r = await call(client, "sd_library_search", {"site": "music", "category": "aaa", "page": 2})
    assert api.calls[0].url.params["c"] == "aaa" and api.calls[0].url.params["pn"] == "2"
    assert r["count"] == 3 and r["items"][0]["item_id"].isdigit() and r["items"][0]["title"]


async def test_library_item_tracks(client, api):
    api["/detailsalbum/"] = page("lib_item.html")
    r = await call(client, "sd_library_item", {"site": "nava", "item_id": "354510"})
    assert r["title"] == "شب هفتم محرم 99 - سید رضا نریمانی"
    assert r["details"]["مداح"] == "سید رضا نریمانی" and r["description"]
    assert len(r["tracks"]) == 7 and r["tracks"][0]["attachment_id"] == "572593" and r["tracks"][0]["length"] == "16:30"


async def test_library_file_links(client, api):
    api["/download/"] = page("lib_download.html")
    r = await call(client, "sd_library_file", {"site": "book", "attachment_id": "411421"})
    assert r["mp3"] == {
        "url": "https://player.iranseda.ir/downloadfile?attid=411421&VALID=TRUE&q=11",
        "size": "22.4 MB",
    }
    assert r["mp4"]["size"] == "45.7 MB"


async def test_library_bad_input_and_empty_page(client, api):
    for name, args in [
        ("sd_library_search", {"site": "book"}),
        ("sd_library_search", {"site": "quran", "query": "حمد"}),
        ("sd_library_search", {"site": "book", "category": "../x"}),
        ("sd_library_item", {"site": "book", "item_id": "abc"}),
        ("sd_library_file", {"site": "book", "attachment_id": "x"}),
    ]:
        assert (await client.call_tool(name, args)).is_error, name
    assert not api.calls
    api["/detailsalbum/"] = lambda request: __import__("httpx").Response(200, text="")
    assert (await client.call_tool("sd_library_item", {"site": "book", "item_id": "5"})).is_error


async def test_program_episode_time_is_zero_padded(client, api):
    import httpx
    from conftest import FIXTURES

    body = (FIXTURES / "program.html").read_text(encoding="utf-8").replace("(ساعت 11:30)", "(ساعت 0:0)", 1)
    api["/program/"] = lambda request: httpx.Response(200, text=body)
    r = await call(client, "sd_program", {"channel": "11", "program_id": "015137"})
    assert r["episodes"][0]["time"] == "00:00"
