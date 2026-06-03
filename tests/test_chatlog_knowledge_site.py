from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from build_chatlog_knowledge_site import (
    ChatMessage,
    build_knowledge_data,
    build_instruction_library,
    classify_link,
    clean_message,
    extract_urls,
    normalize_url,
    parse_chat_file,
    render_link_review_report,
    render_index_html,
    starfleet_website_articles,
)


def test_clean_message_keeps_html_link_label_and_url():
    cleaned = clean_message('Scout <a href="https://example.test/path">Guide</a>')
    assert cleaned == "Scout Guide (https://example.test/path)"


def test_extract_urls_reads_html_and_plain_links():
    urls = extract_urls('See <a href="https://example.test/a">A</a> and https://example.test/b')
    assert "https://example.test/a" in urls
    assert "https://example.test/b" in urls


def test_normalize_url_adds_scheme_and_fixes_known_log_join():
    assert normalize_url("www.thealphasguide.comReporting") == "https://www.thealphasguide.com"


def test_classify_link_redacts_discord_invite():
    category, status, reason, label = classify_link("https://discord.gg/example")
    assert category == "Review before publishing"
    assert status == "redacted"
    assert reason == "private or unclear link"
    assert label == "Discord invite"


def test_classify_link_keeps_public_eve_uni_page():
    category, status, reason, label = classify_link("https://wiki.eveuniversity.org/Missile_mechanics")
    assert category == "Guides and wiki"
    assert status == "public"
    assert reason == "public resource"
    assert "Missile mechanics" in label


def test_parse_chat_file_reads_utf16_eve_log(tmp_path):
    log_path = tmp_path / "Corp_20260603_000000_1.txt"
    log_path.write_text(
        "\r\n"
        "        ---------------------------------------------------------------\r\n"
        "          Channel Name:    Corp\r\n"
        "          Listener:        Test Character\r\n"
        "        ---------------------------------------------------------------\r\n"
        "[ 2026.06.03 01:01:00 ] EVE System > Channel changed to Corp : Star Fleet Productions Academy\r\n"
        "[ 2026.06.03 01:02:03 ] Pilot Name > hostile in Tama\r\n",
        encoding="utf-16",
    )
    messages = parse_chat_file(log_path)
    assert len(messages) == 2
    assert messages[1].channel == "Corp"
    assert messages[1].speaker == "Pilot Name"
    assert messages[1].message == "hostile in Tama"
    assert messages[1].listener == "Test Character"
    assert messages[1].channel_context == "Star Fleet Productions Academy"


def test_instruction_library_reproduces_mining_rules():
    message = ChatMessage(
        timestamp=__import__("datetime").datetime(2026, 6, 3, 1, 2, 3, tzinfo=__import__("datetime").timezone.utc),
        channel="SFU Mining Fleet Rules",
        speaker="EVE System",
        message="SFU Mining Fleet RulesThere are two types of SFU mining fleets - Casual and Official.",
        file_name="SFU Mining Fleet Rules_20260603_000000_1.txt",
    )
    instructions = build_instruction_library(
        {"SFU Mining Fleet Rules": {"rules": message}},
        [message],
    )
    assert instructions[0]["title"] == "SFU Mining Fleet Rules - Full Procedure"
    assert any(section["heading"] == "Official Fleet Rules" for section in instructions[0]["sections"])


def test_instruction_library_redacts_private_links():
    message = ChatMessage(
        timestamp=__import__("datetime").datetime(2026, 6, 3, 1, 2, 3, tzinfo=__import__("datetime").timezone.utc),
        channel="Star Fleet Productions",
        speaker="EVE System",
        message="DISCORD LINK: https://discord.gg/example",
        file_name="Star Fleet Productions_20260603_000000_1.txt",
    )
    instructions = build_instruction_library(
        {"Star Fleet Productions": {"motd": message}},
        [message],
    )
    assert "https://discord.gg/example" not in instructions[0]["raw_reproduction"]
    assert "[redacted: private or unclear link]" in instructions[0]["raw_reproduction"]


def test_rookie_help_is_isolated_from_main_knowledge(tmp_path):
    dt = __import__("datetime")
    rookie = ChatMessage(
        timestamp=dt.datetime(2026, 6, 3, 1, 2, 3, tzinfo=dt.timezone.utc),
        channel="Rookie Help",
        speaker="EVE System",
        message="Channel MOTD: Welcome to Rookie Help. Useful Links: https://wiki.eveuniversity.org/Main_Page",
        file_name="Rookie Help_20260603_000000_1.txt",
    )
    corp = ChatMessage(
        timestamp=dt.datetime(2026, 6, 3, 1, 3, 3, tzinfo=dt.timezone.utc),
        channel="SFU Library",
        speaker="EVE System",
        message="Channel MOTD: Welcome to Star Fleet Union Library",
        file_name="SFU Library_20260603_000000_1.txt",
    )

    data = build_knowledge_data(
        [rookie, corp],
        logs_root=tmp_path,
        since_date="2026-06-03",
        public_safe=True,
    )

    assert all(item["channel"] != "Rookie Help" for item in data["instructions"])
    assert all(item["channel"] != "Rookie Help" for item in data["motds"])
    assert all("Rookie Help" not in [source["channel"] for source in topic["sources"]] for topic in data["topics"])
    assert data["resources"] == []
    assert data["rookie_help"]["instructions"][0]["channel"] == "Rookie Help"
    assert data["rookie_help"]["resources"][0]["label"] == "wiki.eveuniversity.org - Main Page"


def test_non_starfleet_corp_chat_is_filtered_from_main_knowledge(tmp_path):
    dt = __import__("datetime")
    imperial = ChatMessage(
        timestamp=dt.datetime(2026, 6, 3, 1, 2, 3, tzinfo=dt.timezone.utc),
        channel="Corp",
        speaker="Spammer",
        message="HyperNet offer https://example.test/spam",
        file_name="Corp_20260603_000000_1.txt",
        listener="Alt Character",
        channel_context="Imperial Academy",
    )
    starfleet = ChatMessage(
        timestamp=dt.datetime(2026, 6, 3, 1, 3, 3, tzinfo=dt.timezone.utc),
        channel="Corp",
        speaker="EVE System",
        message="Channel MOTD: 1 - Have Fun",
        file_name="Corp_20260603_000001_1.txt",
        listener="Main Character",
        channel_context="Star Fleet Productions Academy",
    )

    data = build_knowledge_data(
        [imperial, starfleet],
        logs_root=tmp_path,
        since_date="2026-06-03",
        public_safe=True,
    )

    assert data["stats"]["character_log_count"] == 2
    assert data["stats"]["filtered_corp_message_count"] == 1
    assert data["resources"] == []
    assert all(source["channel"] != "Imperial Academy" for topic in data["topics"] for source in topic["sources"])


def test_public_starfleet_website_articles_are_included():
    articles = starfleet_website_articles()
    titles = {article["title"] for article in articles}

    assert "Star Fleet Productions Buyback Program Guide" in titles
    assert "Star Fleet Constitution" in titles
    assert all(article["url"].startswith("https://starfleetproductions.space/") for article in articles)


def test_render_index_html_includes_section_jump_navigation():
    html = render_index_html({"meta": {"title": "Test Knowledge"}})

    assert 'class="quick-nav"' in html
    assert 'href="#public-website"' in html
    assert 'href="#resource-database"' in html
    assert 'href="#publish-safety"' in html
    assert 'id="publish-safety"' in html
    assert 'id="safety-checks"' in html
    assert 'id="source-channels"' in html


def test_link_review_report_keeps_full_review_urls_out_of_public_data(tmp_path):
    dt = __import__("datetime")
    message = ChatMessage(
        timestamp=dt.datetime(2026, 6, 3, 1, 2, 3, tzinfo=dt.timezone.utc),
        channel="Corp",
        speaker="EVE System",
        message="Channel MOTD: Discord https://discord.gg/example and wiki https://wiki.eveuniversity.org/Main_Page",
        file_name="Corp_20260603_000000_1.txt",
        listener="Main Character",
        channel_context="Star Fleet Productions Academy",
    )

    data = build_knowledge_data([message], logs_root=tmp_path, since_date="2026-06-03", public_safe=True)
    public_payload = __import__("json").dumps(data)
    report = render_link_review_report(
        [message],
        since_date="2026-06-03",
        generated_at="2026-06-03T00:00:00Z",
    )

    assert "https://discord.gg/example" not in public_payload
    assert "https://discord.gg/example" in report
    assert "https://wiki.eveuniversity.org/Main_Page" not in report
    assert "Local-only report" in report
