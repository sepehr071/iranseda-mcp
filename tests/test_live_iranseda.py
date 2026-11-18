import pytest

pytestmark = [pytest.mark.live, pytest.mark.anyio]


async def test_live_channels_and_stream(client):
    r = (await client.call_tool("sd_channels", {})).structured_content
    assert r["count"] > 15 and any(c["id"] == "11" for c in r["channels"])
    r = (await client.call_tool("sd_live_now", {"channel": "11"})).structured_content
    assert r["stream_url"].endswith(".m3u8") and r["now"]["program"]


async def test_live_schedule_program_episode(client):
    r = (await client.call_tool("sd_schedule", {"channel": "11"})).structured_content
    assert r["count"] > 20 and r["programs"][0]["episode_id"]
    r = (await client.call_tool("sd_search_programs", {"channel": "11", "query": "افق آفتاب"})).structured_content
    assert any(p["program_id"] == "015137" for p in r["programs"])
    r = (await client.call_tool("sd_program", {"channel": "11", "program_id": "015137"})).structured_content
    assert r["name"] == "به افق آفتاب" and r["episodes"][0]["date"]
    r = (
        await client.call_tool("sd_episode", {"channel": "11", "episode_id": r["episodes"][0]["episode_id"]})
    ).structured_content
    assert r["parts"] and r["parts"][0]["mp3_url"].startswith("http")


async def test_live_stream_is_a_playlist(client):
    import httpx

    r = (await client.call_tool("sd_live_now", {"channel": "11"})).structured_content
    async with httpx.AsyncClient(trust_env=False, timeout=20) as h:
        try:
            got = await h.get(r["stream_url"], headers={"Range": "bytes=0-100"})
        except httpx.ConnectTimeout:
            pytest.skip("the site handed out a CDN node (e.g. s2cdn3) that does not answer from this network")
    assert got.status_code in (200, 206) and got.text.startswith("#EXTM3U")


async def test_live_library(client):
    r = (await client.call_tool("sd_library_search", {"site": "book", "query": "اندیشه اسلامی"})).structured_content
    assert r["count"] > 3 and r["items"][0]["item_id"]
    r = (await client.call_tool("sd_library_search", {"site": "quran", "category": "ga"})).structured_content
    assert r["count"] == 20
    r = (await client.call_tool("sd_library_item", {"site": "nava", "item_id": "354510"})).structured_content
    assert len(r["tracks"]) == 7
    r = (await client.call_tool("sd_library_file", {"site": "book", "attachment_id": "411421"})).structured_content
    assert r["mp3"]["url"].startswith("https://player.iranseda.ir/downloadfile")
