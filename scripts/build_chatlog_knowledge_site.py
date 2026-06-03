from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Iterable
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOGS_ROOT = Path.home() / "OneDrive" / "Documents" / "EVE" / "logs"
DEFAULT_OUTPUT_DIR = ROOT / "docs" / "chatlog-knowledge"

CHAT_LINE_RE = re.compile(
    r"^\[\s*(?P<timestamp>\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2})\s*\]\s*"
    r"(?P<speaker>.*?)\s*>\s*(?P<message>.*)$"
)
CHANNEL_NAME_RE = re.compile(r"^\s*Channel\s+Name\s*:\s*(?P<channel>.+?)\s*$", re.IGNORECASE)
LOG_DATE_RE = re.compile(r"_(?P<date>\d{8})_")
HTML_LINK_RE = re.compile(r"<a\s+href=\"(?P<href>[^\"]+)\"[^>]*>(?P<label>.*?)</a>", re.IGNORECASE)
URL_ATTR_RE = re.compile(r"url=(?P<url>[^>\]]+)", re.IGNORECASE)
PLAIN_URL_RE = re.compile(r"https?://[^\s<>\"\]]+|www\.[^\s<>\"\]]+", re.IGNORECASE)
SPACE_RE = re.compile(r"\s+")

PRIVATE_CHANNEL_NAMES = {"private chat", "private chat (2)"}
LINK_ALLOW_DOMAINS = {
    "community.eveonline.com",
    "eve-gatecheck.space",
    "eve-survival.org",
    "forums.eveonline.com",
    "support.eveonline.com",
    "wiki.eveuniversity.org",
    "wiki.signalcartel.space",
    "www.eveonline.com",
    "www.fuzzwork.co.uk",
    "www.wckg.net",
    "zkillboard.com",
    "youtu.be",
    "www.youtube.com",
    "starfleetproductions.space",
    "www.thealphasguide.com",
}
REDACT_DOMAINS = {
    "discord.gg",
    "docs.google.com",
    "frozencrypt.space",
    "rr.harkayn.ovh",
    "tiny.pl",
    "www.game-lavka.ru",
    "www.mmo-games.ru",
}
SPAM_DOMAINS = {"www.game-lavka.ru", "www.mmo-games.ru"}


@dataclass(frozen=True)
class ChatMessage:
    timestamp: datetime
    channel: str
    speaker: str
    message: str
    file_name: str


@dataclass
class LinkRecord:
    url: str
    normalized_url: str
    label: str
    category: str
    status: str
    reason: str
    count: int = 0
    channels: set[str] = field(default_factory=set)

    def to_dict(self) -> dict[str, Any]:
        public_url = self.normalized_url if self.status == "public" else ""
        return {
            "label": self.label,
            "url": public_url,
            "display_url": self.normalized_url if self.status == "public" else f"[redacted: {self.reason}]",
            "category": self.category,
            "status": self.status,
            "reason": self.reason,
            "count": self.count,
            "channels": sorted(self.channels),
        }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logs_root = args.logs_root.expanduser()
    if not logs_root.exists():
        print(f"Logs root does not exist: {logs_root}", file=sys.stderr)
        return 1

    messages = collect_messages(logs_root, since_date=args.since_date)
    if not messages:
        print("No chat messages found for the selected window.", file=sys.stderr)
        return 1

    site_data = build_knowledge_data(
        messages,
        logs_root=logs_root,
        since_date=args.since_date,
        public_safe=not args.include_review_links,
    )
    write_site(args.output_dir.expanduser(), site_data)
    print(f"Wrote chatlog knowledge site to {args.output_dir}")
    print(f"Parsed {site_data['stats']['message_count']} messages from {site_data['stats']['file_count']} files.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a public-safe static knowledge site from recent EVE chat logs.",
    )
    parser.add_argument("--logs-root", type=Path, default=DEFAULT_LOGS_ROOT, help="EVE logs root folder.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Static site output folder.")
    parser.add_argument(
        "--since-date",
        default="",
        help="Include chat log files on or after YYYY-MM-DD. Defaults to the latest log date minus two days.",
    )
    parser.add_argument(
        "--include-review-links",
        action="store_true",
        help="Keep full URLs for review links. Default redacts private, referral, ad, and unclear links.",
    )
    return parser


def collect_messages(logs_root: Path, *, since_date: str = "") -> list[ChatMessage]:
    chatlogs = logs_root / "Chatlogs"
    files = sorted(chatlogs.glob("*.txt")) if chatlogs.exists() else []
    if not files:
        return []

    start_date = parse_since_date(since_date) or latest_log_date(files) - timedelta(days=2)
    messages: list[ChatMessage] = []
    for path in files:
        log_date = log_date_from_name(path)
        if log_date is None or log_date.date() < start_date.date():
            continue
        messages.extend(parse_chat_file(path))
    return [message for message in messages if message.timestamp.date() >= start_date.date()]


def parse_since_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--since-date must be YYYY-MM-DD.") from exc


def latest_log_date(files: Iterable[Path]) -> datetime:
    dates = [date for date in (log_date_from_name(path) for path in files) if date is not None]
    if not dates:
        return datetime.now(timezone.utc)
    return max(dates)


def log_date_from_name(path: Path) -> datetime | None:
    match = LOG_DATE_RE.search(path.name)
    if not match:
        return None
    try:
        return datetime.strptime(match.group("date"), "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_chat_file(path: Path) -> list[ChatMessage]:
    text = read_log_text(path)
    channel = channel_name_from_text(text) or fallback_channel_name(path)
    messages: list[ChatMessage] = []
    for line in text.splitlines():
        match = CHAT_LINE_RE.match(line.lstrip("\ufeff"))
        if not match:
            continue
        try:
            timestamp = datetime.strptime(match.group("timestamp"), "%Y.%m.%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            continue
        messages.append(
            ChatMessage(
                timestamp=timestamp,
                channel=channel,
                speaker=match.group("speaker").strip(),
                message=clean_message(match.group("message")),
                file_name=path.name,
            )
        )
    return messages


def read_log_text(path: Path) -> str:
    prefix = path.read_bytes()[:4]
    encoding = "utf-16" if prefix.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return path.read_text(encoding=encoding, errors="replace")


def channel_name_from_text(text: str) -> str | None:
    for line in text.splitlines()[:80]:
        match = CHANNEL_NAME_RE.match(line)
        if match:
            return match.group("channel").strip()
    return None


def fallback_channel_name(path: Path) -> str:
    return re.sub(r"_\d{8}.*$", "", path.stem).replace("_", "/").strip() or path.stem


def clean_message(value: str) -> str:
    value = html.unescape(value).replace("\ufeff", "")
    value = HTML_LINK_RE.sub(lambda match: f"{match.group('label')} ({match.group('href')})", value)
    value = re.sub(r"<[^>]+>", " ", value)
    return SPACE_RE.sub(" ", value).strip()


def build_knowledge_data(
    messages: list[ChatMessage],
    *,
    logs_root: Path,
    since_date: str,
    public_safe: bool,
) -> dict[str, Any]:
    channels = Counter(message.channel for message in messages)
    files = {message.file_name for message in messages}
    timestamps = [message.timestamp for message in messages]
    motds = unique_motds(messages)
    links = collect_links(messages)
    link_records = [record.to_dict() for record in sorted(links.values(), key=lambda item: (-item.count, item.label))]
    instructions = build_instruction_library(motds, messages)

    return {
        "meta": {
            "title": "EVE Chatlog Knowledge Base",
            "source_root": str(logs_root),
            "generated_at": now_iso(),
            "window_start": min(timestamps).isoformat().replace("+00:00", "Z"),
            "window_end": max(timestamps).isoformat().replace("+00:00", "Z"),
            "since_date": since_date or "latest log date minus two days",
            "public_safe": public_safe,
            "privacy_note": (
                "Raw chat logs, private chat channels, player-by-player transcripts, referral links, ad/spam links, "
                "private-looking invites, and unreviewed external links are not published in the public-safe build. "
                "Instruction text is reproduced from channel MOTDs with risky links redacted."
            ),
        },
        "stats": {
            "file_count": len(files),
            "message_count": len(messages),
            "channel_count": len(channels),
            "motd_count": sum(len(items) for items in motds.values()),
            "instruction_count": len(instructions),
            "link_count": len(link_records),
            "public_link_count": sum(1 for item in link_records if item["status"] == "public"),
            "review_link_count": sum(1 for item in link_records if item["status"] != "public"),
        },
        "channels": [
            {"name": name, "message_count": count}
            for name, count in channels.most_common()
            if name.casefold() not in PRIVATE_CHANNEL_NAMES
        ],
        "instructions": instructions,
        "topics": build_topics(motds, messages),
        "resources": link_records,
        "motds": motd_summaries(motds),
    }


def unique_motds(messages: list[ChatMessage]) -> dict[str, dict[str, ChatMessage]]:
    result: dict[str, dict[str, ChatMessage]] = defaultdict(dict)
    for message in messages:
        if "Channel MOTD:" not in message.message:
            continue
        body = message.message.split("Channel MOTD:", 1)[1].strip()
        key = SPACE_RE.sub(" ", body).casefold()
        result[message.channel].setdefault(key, ChatMessage(message.timestamp, message.channel, "", body, message.file_name))
    return result


def collect_links(messages: list[ChatMessage]) -> dict[str, LinkRecord]:
    records: dict[str, LinkRecord] = {}
    for message in messages:
        if message.channel.casefold() in PRIVATE_CHANNEL_NAMES:
            continue
        for raw_url in extract_urls(message.message):
            normalized = normalize_url(raw_url)
            if not normalized:
                continue
            record = records.get(normalized)
            if record is None:
                category, status, reason, label = classify_link(normalized)
                record = LinkRecord(
                    url=raw_url,
                    normalized_url=normalized,
                    label=label,
                    category=category,
                    status=status,
                    reason=reason,
                )
                records[normalized] = record
            record.count += 1
            record.channels.add(message.channel)
    return records


def build_instruction_library(
    motds: dict[str, dict[str, ChatMessage]],
    messages: list[ChatMessage],
) -> list[dict[str, Any]]:
    instructions: list[dict[str, Any]] = []
    for channel in [
        "SFU Mining Fleet Rules",
        "Starfleet (Ship/Fit/Mod Ordering)",
        "Z-S Overview",
        "Star Fleet Productions",
        "SFU Recruitment",
        "SFU Library",
        "Rookie Help",
        "SFU Study Hall",
        "SFU Study Hall PT.2",
        "SFU Doctrine 3.2",
        "SFU Doctrine",
        "SFU Mining Fits",
    ]:
        entry = first_motd_entry(motds, channel)
        if entry is None:
            continue
        instructions.append(instruction_for_channel(channel, entry, messages))
    return instructions


def first_motd_entry(motds: dict[str, dict[str, ChatMessage]], channel: str) -> ChatMessage | None:
    entries = list(motds.get(channel, {}).values())
    if not entries:
        return None
    entries.sort(key=lambda item: item.timestamp)
    return entries[-1]


def instruction_for_channel(channel: str, entry: ChatMessage, messages: list[ChatMessage]) -> dict[str, Any]:
    channel_messages = [message for message in messages if message.channel == channel]
    seen_range = {
        "first_seen": min(message.timestamp for message in channel_messages).isoformat().replace("+00:00", "Z"),
        "last_seen": max(message.timestamp for message in channel_messages).isoformat().replace("+00:00", "Z"),
        "message_count": len(channel_messages),
    } if channel_messages else {}
    sections = instruction_sections(channel)
    if not sections:
        sections = [{"heading": "Reproduced MOTD", "items": [redact_sensitive_text(entry.message)]}]
    return {
        "id": slugify(channel),
        "channel": channel,
        "title": instruction_title(channel),
        "summary": summarize_motd(channel, entry.message),
        "seen_at": entry.timestamp.isoformat().replace("+00:00", "Z"),
        "source": "Channel MOTD",
        "seen_range": seen_range,
        "sections": sections,
        "raw_reproduction": redact_sensitive_text(entry.message),
    }


def instruction_title(channel: str) -> str:
    titles = {
        "SFU Mining Fleet Rules": "SFU Mining Fleet Rules - Full Procedure",
        "Starfleet (Ship/Fit/Mod Ordering)": "Starfleet Ship, Fit, And Module Ordering Procedure",
        "Z-S Overview": "Z-S Overview Setup Procedure",
        "Star Fleet Productions": "Unified Star Fleet Chat Expectations And Staff Notes",
        "SFU Recruitment": "Star Fleet Union Recruitment Brief",
        "SFU Library": "SFU Library Channel Directory",
        "Rookie Help": "Rookie Help Channel Rules And Starter References",
        "SFU Study Hall": "SFU Study Hall Resource Index",
        "SFU Study Hall PT.2": "SFU Study Hall PT.2 Resource Index",
        "SFU Doctrine 3.2": "SFU Doctrine 3.2 Faction Warfare Fit Index",
        "SFU Doctrine": "SFU Doctrine Fit Index",
        "SFU Mining Fits": "SFU Mining Fits Index",
    }
    return titles.get(channel, channel)


def instruction_sections(channel: str) -> list[dict[str, Any]]:
    manual: dict[str, list[dict[str, Any]]] = {
        "SFU Mining Fleet Rules": [
            {
                "heading": "Fleet Types",
                "items": [
                    "SFU mining fleets are split into Casual fleets and Official fleets.",
                    "Rules in the common section apply to both fleet types.",
                ],
            },
            {
                "heading": "Rules For Both Casual And Official Fleets",
                "items": [
                    "1.1 Do not abuse or goad other pilots in local chat. The MOTD emphasizes that this is very important.",
                    "1.2 The fleet commander must safeguard miner security.",
                    "1.2.1 The FC should not go AFK while leading the fleet.",
                    "1.2.2 The FC must be prepared to kill NPC rats.",
                    "1.2.3 The FC must observe local chat and warn the fleet about red or orange spikes.",
                    "1.3 Every mining fleet MOTD must link back to the mining fleet rules.",
                    "1.4 Ship Replacement Program coverage is only given to pilots flying the SFU Procurer.",
                ],
            },
            {
                "heading": "Casual Fleet Rules",
                "items": [
                    "2.1 Miners self-haul their own ore.",
                    "2.2 Fleet commanders should not offer alternative buyback programs without permission. Promoting the official SFU buyback program is allowed.",
                ],
            },
            {
                "heading": "Official Fleet Rules",
                "items": [
                    "3.1 Only mining ships are allowed to mine. PvP ships may be present to guard, but may not mine.",
                    "3.2 Miners jetcan ore to the FC.",
                    "3.3 Do not fly an Orca or Porpoise without FC permission.",
                    "3.4 Official mining fleets may only be led by pilots with the Mining Foreman or Mining Commander titles.",
                    "3.5 FCs must pay miners the correct rate for all jetcans: 90 percent Jita buy price for ore, moon ore, gas, and ice.",
                    "3.6 The MOTD recommends that miners do not fly Coveter, Retriever, or Hulk. Mackinaw is allowed if it is at or near 70k EHP.",
                ],
            },
            {"heading": "Reporting", "items": ["Rule violations should be reported to Drakon Trelos."]},
        ],
        "Starfleet (Ship/Fit/Mod Ordering)": [
            {
                "heading": "Purpose",
                "items": [
                    "Use this channel/process when you need a ship, a specific fit, modules, hulls, or delivered items instead of buying from a market hub.",
                    "The MOTD names Amarr, Providence, and Catch blue-region delivery areas.",
                ],
            },
            {
                "heading": "How To Submit An Order",
                "items": [
                    "Send the order to Drakon Trelos or General Gasanov.",
                    "Use the subject line: Starfleet Order.",
                    "Include item name or fit link.",
                    "Include quantity.",
                    "Include destination station.",
                    "Include the timeframe you need it by.",
                    "Include any special notes.",
                ],
            },
            {
                "heading": "Timing And Response",
                "items": [
                    "Capital ships can sometimes take up to three weeks to deliver, depending on the ship.",
                    "After the order is received, the order team replies within 24 to 48 hours with price and comments.",
                    "After that reply, you may accept or refuse the order.",
                ],
            },
            {
                "heading": "Example Order Format",
                "items": [
                    "Subject: Starfleet Order",
                    "Simulated Venture Fitting x 10 units",
                    "Liquid Ozone x 5000 units",
                    "Timeframe: 1 week",
                    "Location: KBP7-G - Heir of Athra",
                    "Note: Please send an in-game message once the contract is ready.",
                ],
            },
        ],
        "Z-S Overview": [
            {
                "heading": "Setup Steps",
                "items": [
                    "1. Open Overview Settings.",
                    "2. On the Misc tab, click Reset All Overview Settings.",
                    "3. Click the Core pack, then add any additional packs you want or need.",
                    "4. Click a layout.",
                    "5. Refresh brackets, then click the Ships tab in Overview Settings.",
                ],
            },
            {
                "heading": "Referenced Packs And Layouts",
                "items": [
                    "Z-S v10.04.30 Core is listed as required.",
                    "Z-S v10.04.30 Targets is listed as required.",
                    "Z-S v10.04.30 Compact is listed as a layout option.",
                    "Z-S v9.09b Incursion is listed as an incursion pack option.",
                    "Z-S Fleet Broadcast Settings v0.5 is listed for fleet broadcast settings.",
                ],
            },
        ],
        "Star Fleet Productions": [
            {
                "heading": "Chat Expectations",
                "items": [
                    "1. Be respectful of each other in chats.",
                    "2. If there are issues or tension, take the chat to a private chat.",
                    "3. Violators may be removed from the chat and, depending on severity, may be removed from corp.",
                ],
            },
            {
                "heading": "Miner Notes",
                "items": [
                    "Check the calendar for schedule information.",
                    "If near Oipo/Jita, watch the AO Mining mailing list.",
                    "Mining as a fleet is encouraged.",
                    "Review corporation bulletins.",
                ],
            },
            {
                "heading": "Communications And Contacts",
                "items": [
                    "Discord is used for out-of-game text and voice communication. The public-safe build redacts the invite URL.",
                    "Important mail lists mentioned: AO mining, Star Fleet Mining, and AO Admin.",
                    "Issues or concerns should be taken to Drakon Trelos.",
                ],
            },
        ],
        "SFU Recruitment": [
            {
                "heading": "Corps To Choose From",
                "items": [
                    "Star Fleet Section 31: Null-sec PvP corp focused on regional defense, solo PvP, and group PvP.",
                    "Star Fleet Productions: Null-sec corp for industry, mining, exploration, ratting, and PvP reinforcements.",
                    "Star Fleet Productions Academy: High-sec based corp for helping new and returning capsuleers and high-sec life.",
                ],
            },
            {
                "heading": "Alliance Goal",
                "items": [
                    "The stated goal is to build a home for both null-sec and high-sec divisions close enough to help one another.",
                    "Discord is used for comms. The public-safe build redacts the invite URL.",
                ],
            },
            {
                "heading": "Leadership And Liaisons",
                "items": [
                    "Command staff listed: Drakon Trelos as Fleet Admiral and General Gasanov as Admiral/Russian liaison.",
                    "Alliance liaison roles include German, Spanish, French, and Russian contacts.",
                    "The MOTD notes that more liaisons are welcome.",
                ],
            },
        ],
        "SFU Library": [
            {
                "heading": "SFU Chat Channels",
                "items": [
                    "Star Fleet Productions: alliance and academy unified chat.",
                    "Starfleet (Ship/Fit/Mod Ordering): ship, fit, and module ordering.",
                    "SFU Study Hall and SFU Study Hall PT.2: reference and training channels.",
                    "SFU Video Library and SFU Video Library PT.2: video reference channels.",
                    "SFU Mining Fleet Rules: mining fleet rules.",
                    "SFU Recruitment: recruitment channel.",
                ],
            },
            {
                "heading": "Ally And Intel Channels",
                "items": [
                    "Absolute Order: ally unified chat.",
                    "south moon alliance time: high-sec allied moon timers.",
                    "Null Focused is marked alliance-only.",
                    "Providence_Intel is marked alliance-only.",
                ],
            },
            {
                "heading": "Doctrine, Training, And Command References",
                "items": [
                    "Doctrine fit channels include SFU Doctrine and SFU Mining Fits.",
                    "Ally fit channels include SFU Ally Bombers, SFU Ally Faction Warfare, SFU Null Defense 1.0, and SFU Null Defense 1.1.",
                    "Training plans include AO TP Fatherland/FW and AO TP FEAR/FW.",
                    "SFU Upper Command is for upper-command staff.",
                ],
            },
        ],
        "Rookie Help": [
            {
                "heading": "Channel Purpose",
                "items": ["Rookie Help is for EVE-related questions and is English-only."],
            },
            {
                "heading": "Prohibited In Rookie Help",
                "items": [
                    "Trading or other financial interactions.",
                    "Begging.",
                    "Recruitment, referral links, freelance project links, soliciting, or advertising.",
                    "Political discussion, in-game or out-of-game.",
                    "Other off-topic discussion.",
                    "Swearing, trolling, spam, typing in caps, text decoration, or other disruptive behavior.",
                    "Unsolicited recruitment or referral mails to channel members.",
                ],
            },
            {
                "heading": "Starter References Named In The MOTD",
                "items": [
                    "EVE Academy, patch notes, support tickets, game rules, EULA, Terms of Service, exploits, and Discord are named as useful official references.",
                    "Advanced tutorial references include Career Agents and the level 1 Sisters of EVE epic arc from Sister Alitura in Arnon.",
                    "EVE University Wiki and The Alpha's Guide are named as starter learning references.",
                    "Bug reports should be filed with F12, then Report Bug.",
                ],
            },
        ],
        "SFU Study Hall": [
            {
                "heading": "Market And Industry Tools",
                "items": [
                    "Appraisal Tool v1.1, Janice, Evetycoon, Evetrade, Eve Harvest, Cerlestes Ore Prices, Thonky Planet Checker, Eve-webtools PI, hanns.io/pi, and Eve-cost Calculator are listed.",
                ],
            },
            {
                "heading": "Travel, PvP, And PvE References",
                "items": [
                    "Travel/PvP references include Gatecheck, Dotlan Maps, zKillboard, Battle Reports, Dscan.info, and Localthreat.",
                    "PvE references include insta undock/dock setup, MWD/Cloak trick, LP store lists, mission cheatsheets, NPC damage, NPCs to deal and heal, and Rykki's PvE wormhole guide.",
                ],
            },
        ],
        "SFU Study Hall PT.2": [
            {
                "heading": "General And Skill References",
                "items": [
                    "General references include a ship fitting tool, the Comprehensive Guide to EVE, test server info, EveWho, and the Magic 14 skills.",
                    "Mining/PI references include mining crystals and PI grid.",
                ],
            },
            {
                "heading": "Wormhole, Hauling, Fleet, And SFU References",
                "items": [
                    "Wormhole references include wormhole directory and anoik.is/wormholes.",
                    "Hauling references include AO HS Hauling Guide, Red Frog, and PushX.",
                    "Fleet references include fleet terminology and overview/Z-S references.",
                    "SFU references include Human Resources, 8 Stars of Success, departments and ranks, buyback, blueprint library, and reaction formula library.",
                ],
            },
        ],
        "SFU Doctrine 3.2": [
            {
                "heading": "Faction Warfare Elite",
                "items": [
                    "Command: AO Fury Command Ship FW TFS Pontifex.",
                    "DPS: AO Fury DPS Corm Storm T1 Cormorant, Corm Storm T2 Cormorant, and FW Insurgency Algos.",
                    "Tackle: AO Fury Tackle FW Insurgence Condor.",
                    "Cruiser DPS: AO Fury Cruiser DPS Exequror, Exequror Navy Issue, and Thorax.",
                ],
            },
            {
                "heading": "Faction Warfare Basic",
                "items": [
                    "[FW TFS] Exequror.",
                    "[FW TFS] Thorax.",
                    "[FW TFS] Thorax L1.",
                    "[FW TFS] Exequror NI.",
                    "[FW TFS] Exequror NI Rails.",
                    "[FW TFS] Cormorant T1.",
                    "[FW TFS] Cormorant T2.",
                    "[FW TFS] Catalyst.",
                ],
            },
        ],
        "SFU Doctrine": [
            {
                "heading": "Doctrine Index",
                "items": [
                    "The SFU Doctrine channel is captured as a doctrine-fit reference channel. Its MOTD should be reviewed in-game for exact fits before fleets.",
                ],
            },
        ],
        "SFU Mining Fits": [
            {
                "heading": "Mining Fit Index",
                "items": [
                    "The SFU Mining Fits channel is captured as a mining-fit reference channel. Its MOTD should be reviewed in-game for exact fits before use.",
                ],
            },
        ],
    }
    return manual.get(channel, [])


def redact_sensitive_text(value: str) -> str:
    text = value
    for url in extract_urls(value):
        normalized = normalize_url(url)
        if not normalized:
            continue
        _, status, reason, _ = classify_link(normalized)
        if status != "public":
            text = text.replace(url, f"[redacted: {reason}]")
    return text


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or "item"


def extract_urls(message: str) -> list[str]:
    urls: list[str] = []
    for match in HTML_LINK_RE.finditer(message):
        urls.append(match.group("href"))
    for match in URL_ATTR_RE.finditer(message):
        urls.append(match.group("url"))
    for match in PLAIN_URL_RE.finditer(message):
        urls.append(match.group(0))
    return urls


def normalize_url(raw_url: str) -> str:
    url = html.unescape(raw_url).strip().strip(".,;)")
    if not url:
        return ""
    if url.casefold().startswith("www."):
        url = "https://" + url
    url = fix_known_log_join(url)
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return ""
    host = parsed.netloc.casefold()
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    fragment = f"#{parsed.fragment}" if parsed.fragment else ""
    return f"{parsed.scheme.lower()}://{host}{path}{query}{fragment}"


def fix_known_log_join(url: str) -> str:
    lower = url.casefold()
    if lower == "https://www.thealphasguide.comreporting":
        return "https://www.thealphasguide.com"
    if lower == "http://wiki.eveuniversity.org/the":
        return "http://wiki.eveuniversity.org/The"
    return url


def classify_link(url: str) -> tuple[str, str, str, str]:
    parsed = urlparse(url)
    host = parsed.netloc.casefold()
    path = parsed.path.strip("/")
    label = label_for_link(host, path)

    if "signup" in path.casefold() and "invc=" in parsed.query.casefold():
        return "Review before publishing", "redacted", "referral link", "Referral link"
    if host in SPAM_DOMAINS:
        return "Rejected / spam seen in chat", "redacted", "ISK sale or ad link", "Rejected ISK-sale/ad link"
    if host in REDACT_DOMAINS or host.endswith(".discord.gg"):
        safe_label = "Discord invite" if host == "discord.gg" else "Redacted review link"
        return "Review before publishing", "redacted", "private or unclear link", safe_label
    if host in LINK_ALLOW_DOMAINS:
        if url == "http://wiki.eveuniversity.org/The":
            return "Review before publishing", "redacted", "truncated Rookie Help MOTD link", label
        return category_for_domain(host, path), "public", "public resource", label
    return "Review before publishing", "redacted", "unclassified external link", label


def label_for_link(host: str, path: str) -> str:
    if host == "starfleetproductions.space":
        return "Star Fleet Productions website"
    if host == "www.thealphasguide.com":
        return "The Alpha's Guide"
    if host == "discord.gg":
        return "Discord invite"
    if host == "www.youtube.com" or host == "youtu.be":
        return "YouTube video"
    if host == "eve-gatecheck.space":
        return "EVE Gatecheck route safety"
    if host == "zkillboard.com":
        return "zKillboard reference"
    if host == "www.fuzzwork.co.uk":
        return "Fuzzwork LP Store"
    if host == "wiki.signalcartel.space":
        return "Signal Cartel exploration safety guide"
    if host == "eve-survival.org":
        return "EVE Survival mission report"
    if host in {"www.wckg.net", "wiki.eveuniversity.org", "support.eveonline.com", "forums.eveonline.com"}:
        title = path.rsplit("/", 1)[-1].replace("_", " ").replace("-", " ").strip()
        return f"{host} - {title}" if title else host
    if host in {"www.eveonline.com", "community.eveonline.com"}:
        title = path.rsplit("/", 1)[-1].replace("-", " ").strip()
        return f"EVE Online - {title}" if title else "EVE Online"
    if host == "docs.google.com":
        return "Google Sheets link"
    return host


def category_for_domain(host: str, path: str) -> str:
    if host in {"www.eveonline.com", "support.eveonline.com", "community.eveonline.com", "forums.eveonline.com"}:
        return "Official EVE"
    if host in {
        "wiki.eveuniversity.org",
        "www.thealphasguide.com",
        "www.wckg.net",
        "wiki.signalcartel.space",
        "eve-survival.org",
    }:
        return "Guides and wiki"
    if host in {"eve-gatecheck.space", "zkillboard.com", "www.fuzzwork.co.uk"}:
        return "Tools"
    if host in {"www.youtube.com", "youtu.be"}:
        return "Videos"
    if host == "starfleetproductions.space":
        return "Corp / alliance"
    return "Public resource"


def build_topics(motds: dict[str, dict[str, ChatMessage]], messages: list[ChatMessage]) -> list[dict[str, Any]]:
    return [
        {
            "id": "corp-orientation",
            "title": "Corp Orientation",
            "category": "Corp operations",
            "summary": (
                "The corp MOTD frames Star Fleet as a practical, social home base: have fun, make ISK, use "
                "Discord for questions, watch corporation bulletins, and use the unified Star Fleet chat for "
                "high-sec and null-sec members."
            ),
            "details": [
                "The SFU Library points members to unified chat, study channels, doctrine fits, mining rules, recruitment, and ally channels.",
                "Recruitment text describes three tracks: high-sec academy, null-sec industry/general activity, and null-sec PvP/defense.",
                "Star Fleet Productions Academy is the high-sec home for new and returning capsuleers; Star Fleet Productions is the null-sec industry/general activity corp; Star Fleet Section 31 is the null-sec PvP/defense corp.",
                "The alliance goal described in recruitment is to keep high-sec and null-sec divisions close enough that they can help each other.",
                "Recent corp chat also emphasized moving toward corp HQ, Sisters of EVE epic arc questions, mining, exploration, manufacturing, and basic local-defense readiness.",
                "Leadership contacts and liaisons are named in the recruitment MOTD; exact personal outreach should still be checked in-game because roles can change.",
            ],
            "sources": source_summary(["Corp", "SFU Library", "SFU Recruitment"], motds, messages),
        },
        {
            "id": "chat-expectations",
            "title": "Chat Expectations",
            "category": "Rules",
            "summary": (
                "Internal chat expectations are simple: be respectful, move tension or disputes to private chat, "
                "and treat serious chat problems as a leadership issue."
            ),
            "details": [
                "Rookie Help has stricter public-channel rules: no trading, begging, recruitment, referral links, advertising, politics, off-topic disruption, swearing, trolling, spam, caps abuse, or text decoration.",
                "Recruitment/referral contact should use recruitment channels instead of unsolicited mails.",
                "Star Fleet internal chat asks members to be respectful and move tension into private chat instead of letting public channels escalate.",
                "Star Fleet Productions' MOTD says chat violations can lead to removal from the chat and, depending on severity, possible removal from corp.",
                "The mining fleet rules separately warn pilots not to abuse or goad other pilots in local chat.",
            ],
            "sources": source_summary(["Star Fleet Productions", "Rookie Help"], motds, messages),
        },
        {
            "id": "mining-rules",
            "title": "Mining Fleet Rules",
            "category": "Mining",
            "summary": (
                "SFU mining fleets are split into casual and official fleets. The common rule is safety first: "
                "do not abuse pilots in local, FCs must stay present, watch local, handle NPC rats, and link the "
                "rules in the fleet MOTD."
            ),
            "details": [
                "Casual fleets: miners self-haul ore, and unofficial buyback programs need permission.",
                "Official fleets: mining ships mine, PvP ships may guard, miners jetcan ore to the FC, and Orca/Porpoise use needs FC permission.",
                "Official FCs should have the Mining Foreman or Mining Commander title and pay the stated 90 percent Jita buy rate for ore, moon ore, gas, and ice.",
                "SRP is tied to the SFU Procurer; the rules recommend avoiding Coveter, Retriever, or Hulk in fleet contexts, while Mackinaw is acceptable near 70k EHP.",
                "FCs are explicitly responsible for miner security: do not lead while AFK, be prepared for NPC rats, and watch local for red/orange spikes.",
                "Recent fleet chat reinforced the practical side: compress ore before hauling, use doctrine fits when possible, and fleet mining helps the corp produce more ships.",
            ],
            "sources": source_summary(["SFU Mining Fleet Rules", "Fleet", "Corp"], motds, messages),
        },
        {
            "id": "mining-and-industry-notes",
            "title": "Mining And Industry Notes",
            "category": "Mining",
            "summary": (
                "The useful recurring advice is to compress ore, mine with fleets when possible, and treat ship choice "
                "as a balance of yield, survivability, and logistics."
            ),
            "details": [
                "Venture was described as fragile but useful for sneaky low-sec gas huffing.",
                "Endurance was called a better ore miner than Venture and can use Improved Cloaking Device II.",
                "Retriever plus Strip Miner I was suggested as a practical starter step before modulated strip miners and crystals.",
                "Fleet chat explained doctrine fits as standardized fits that are easier for the corp to sustain and replace.",
                "Progression advice in corp chat mentioned training Strip Miner I first, then Modulated Strip Miners, then the correct crystals.",
                "A Hulk was described as very fast for mining with the right fit but lacking cargo capacity; this should be balanced against fleet SRP and survivability rules.",
                "Low-sec mining was discussed as possible, but the surrounding rules make security awareness and local monitoring central rather than optional.",
            ],
            "sources": source_summary(["Corp", "Fleet", "SFU Mining Fleet Rules"], motds, messages),
        },
        {
            "id": "missions-combat-sites",
            "title": "Missions, Combat Sites, And Fitting",
            "category": "PvE",
            "summary": (
                "The logs contain practical starter PvE advice: train skills and T2 weapons for DPS, do not underskill "
                "expensive ships, treat combat-site difficulty labels carefully, and be ready to warp out."
            ),
            "details": [
                "A chat explanation said level 1.3 sites can be harder than level 2.0 sites, and suggested trying Refuge-level sites before tougher Dens.",
                "Dens were described as level 4 difficulty and not something to run casually without a good fit.",
                "Level 3 and 4 missions require NPC corporation standing; level 4 missions were described as strong money and salvage sources.",
                "Fleeted mission running can let one pilot accept missions while everyone shares completion rewards.",
                "Mobile Tractor Units loot wrecks but do not salvage them; salvage drones or salvagers are still needed.",
                "For DPS gains, corp chat emphasized T2 weapons and trained support skills before chasing more expensive ships.",
                "Underskilling a ship was called a good way to lose it; fit confidence and escape readiness matter more than rushing into harder sites.",
                "Salvage was described as more worthwhile around higher-level missions, especially level 4s, but salvage drones can be carried for convenience.",
            ],
            "sources": source_summary(["Corp", "Rookie Help"], motds, messages),
        },
        {
            "id": "resource-map",
            "title": "Guide And Tool Map",
            "category": "Resources",
            "summary": (
                "The recent logs repeatedly point to EVE University, WCKG, EVE Gatecheck, zKillboard, Fuzzwork LP Store, "
                "Signal Cartel safety material, EVE Survival, and official EVE pages."
            ),
            "details": [
                "Guide clusters include exploration, abyssals, incursions, combat sites, planetary industry, standings, timers, missile mechanics, turret mechanics, and Alpha/Omega basics.",
                "Tool clusters include route safety, kill reports, LP store research, market/appraisal references, PI references, wormhole references, hauling services, and overview setup.",
                "The public-safe build redacts Discord invites, referral links, ad-like ISK sale links, private-looking tools, and raw Google Sheets URLs until someone reviews them.",
                "Study Hall also names market/appraisal and industry tools such as Janice, Evetycoon, Evetrade, Cerlestes, Thonky, Eve-webtools PI, hanns.io/pi, and Eve-cost Calculator.",
                "Study Hall PT.2 adds ship fitting tools, Magic 14, mining crystals, PI grid, wormhole directory references, hauling services, fleet terminology, Z-S overview, SFU buyback, blueprint library, and reaction formula library.",
            ],
            "sources": source_summary(["Rookie Help", "SFU Study Hall", "SFU Study Hall PT.2", "Z-S Overview"], motds, messages),
        },
        {
            "id": "orders-and-logistics",
            "title": "Ship, Fit, And Mod Ordering",
            "category": "Logistics",
            "summary": (
                "The Starfleet ordering channel gives a simple procurement workflow for hulls, modules, and fits delivered "
                "around Amarr, Providence, and Catch blue-region logistics."
            ),
            "details": [
                "Use an in-game mail subject of Starfleet Order.",
                "Include item or fit link, quantity, destination station, desired timeframe, and special notes.",
                "Capital ships can take up to about three weeks depending on the request.",
                "The order team replies with price and comments, after which the request can be accepted or declined.",
                "The example order includes both fitted ships and bulk materials, showing that the process is not only for whole hulls.",
                "Delivery areas named in the MOTD include Amarr, Providence, and Catch blue-region logistics.",
            ],
            "sources": source_summary(["Starfleet (Ship/Fit/Mod Ordering)"], motds, messages),
        },
        {
            "id": "overview-and-doctrines",
            "title": "Overview And Doctrine References",
            "category": "Fitting",
            "summary": (
                "The logs include overview setup references and doctrine fit channels. Z-S Overview instructions say to reset "
                "overview settings, load the core pack, add desired packs, choose a layout, and refresh brackets."
            ),
            "details": [
                "Doctrine references include faction warfare command, DPS, tackle, cruiser DPS, and basic FW fits.",
                "Study Hall channels list fitting tools, Magic 14 skills, mining crystals, PI grid, wormhole references, hauling services, fleet terminology, and SFU buyback/library references.",
                "Z-S Overview setup has a concrete sequence: reset overview settings, load core and optional packs, pick a layout, refresh brackets, and update the Ships tab.",
                "Doctrine channels should be treated as indexes, not final fit validation; check the in-game channel before fleet use because doctrine fits can change.",
            ],
            "sources": source_summary(["Z-S Overview", "SFU Doctrine 3.2", "SFU Study Hall PT.2"], motds, messages),
        },
    ]


def source_summary(channel_names: list[str], motds: dict[str, dict[str, ChatMessage]], messages: list[ChatMessage]) -> list[dict[str, Any]]:
    source_items: list[dict[str, Any]] = []
    for channel in channel_names:
        channel_messages = [message for message in messages if message.channel == channel]
        if not channel_messages:
            continue
        source_items.append(
            {
                "channel": channel,
                "message_count": len(channel_messages),
                "motd_blocks": len(motds.get(channel, {})),
                "first_seen": min(message.timestamp for message in channel_messages).isoformat().replace("+00:00", "Z"),
                "last_seen": max(message.timestamp for message in channel_messages).isoformat().replace("+00:00", "Z"),
            }
        )
    return source_items


def motd_summaries(motds: dict[str, dict[str, ChatMessage]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for channel, entries in sorted(motds.items()):
        if channel.casefold() in PRIVATE_CHANNEL_NAMES:
            continue
        for entry in entries.values():
            result.append(
                {
                    "channel": channel,
                    "seen_at": entry.timestamp.isoformat().replace("+00:00", "Z"),
                    "summary": redact_sensitive_text(summarize_motd(channel, entry.message)),
                }
            )
    return result


def summarize_motd(channel: str, body: str) -> str:
    trimmed = SPACE_RE.sub(" ", body).strip()
    summaries = {
        "SFU Mining Fleet Rules": "Rules for casual and official mining fleets, FC duties, ore handling, SRP, and payout expectations.",
        "SFU Library": "Directory of SFU, ally, doctrine, training, command, and reference channels.",
        "Rookie Help": "Public Rookie Help rules and starter learning references.",
        "Star Fleet Productions": "Unified high-sec/null-sec staff chat expectations, miner notes, Discord, mailing lists, and videos.",
        "Starfleet (Ship/Fit/Mod Ordering)": "Order process for hulls, fits, modules, delivery location, timeframe, and quote approval.",
        "SFU Recruitment": "Recruitment overview for SFU high-sec academy and null-sec corps.",
        "Z-S Overview": "Z-S Overview setup steps, packs, layouts, and fleet broadcast settings.",
        "SFU Study Hall": "Market, mining, PI, travel, PvP, PvE, mission, and wormhole reference index.",
        "SFU Study Hall PT.2": "General references, fitting tools, Magic 14, PI, wormholes, hauling, fleet terminology, and SFU resources.",
        "SFU Doctrine 3.2": "Faction warfare doctrine list covering command, DPS, tackle, cruiser DPS, and basic fits.",
    }
    return summaries.get(channel, trimmed[:280] + ("..." if len(trimmed) > 280 else ""))


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def write_site(output_dir: Path, data: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "knowledge.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (output_dir / "index.html").write_text(render_index_html(data), encoding="utf-8")
    (output_dir / "README.md").write_text(render_site_readme(data), encoding="utf-8")


def render_index_html(data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(data["meta"]["title"])}</title>
  <style>{SITE_CSS}</style>
</head>
<body>
  <header class="site-header">
    <div>
      <p class="eyebrow">Public-safe EVE chatlog digest</p>
      <h1>EVE Chatlog Knowledge Base</h1>
      <p class="lede">A clarified, downloadable summary of recent rules, guides, tools, channels, and practical advice found in local EVE chat logs.</p>
    </div>
    <div class="stamp">
      <span>Window</span>
      <strong id="window-range">Loading</strong>
    </div>
  </header>
  <main>
    <section class="stats" id="stats"></section>
    <section class="notice">
      <strong>Publishing guard:</strong>
      <span id="privacy-note"></span>
    </section>
    <section class="toolbar">
      <label>
        <span>Search</span>
        <input id="search" type="search" placeholder="Search instructions, topics, resources, channels">
      </label>
      <label>
        <span>Resource status</span>
        <select id="status-filter">
          <option value="all">All resources</option>
          <option value="public">Public links</option>
          <option value="redacted">Review/redacted</option>
        </select>
      </label>
    </section>
    <section>
      <div class="section-heading">
        <h2>Instruction Library</h2>
        <p>High-fidelity reproductions of detailed channel instructions, reformatted for reading and public-safe sharing.</p>
      </div>
      <div class="instruction-list" id="instructions"></div>
    </section>
    <section>
      <div class="section-heading">
        <h2>Clarified Knowledge</h2>
        <p>Fuller classified summaries built from repeated MOTDs and useful recent chat advice.</p>
      </div>
      <div class="topic-grid" id="topics"></div>
    </section>
    <section>
      <div class="section-heading">
        <h2>Resource Database</h2>
        <p>Links discovered in logs. Public-safe entries are clickable; risky or private-looking entries are redacted for review.</p>
      </div>
      <div class="resource-table" id="resources"></div>
    </section>
    <section>
      <div class="section-heading">
        <h2>Source Channels</h2>
        <p>Recent channel activity included in the digest. Private chat transcripts are not published.</p>
      </div>
      <div class="channel-list" id="channels"></div>
    </section>
  </main>
  <script type="application/json" id="knowledge-data">{payload}</script>
  <script>{SITE_JS}</script>
</body>
</html>
"""


def render_site_readme(data: dict[str, Any]) -> str:
    return f"""# EVE Chatlog Knowledge Base

This folder contains a static, public-safe knowledge website generated from recent local EVE chat logs.

- Open `index.html` in a browser.
- `knowledge.json` is the structured database for reuse in other tools.
- Raw chat logs are not included.
- Generated at: {data["meta"]["generated_at"]}
- Source window: {data["meta"]["window_start"]} to {data["meta"]["window_end"]}

Regenerate from the repository root:

```powershell
.\\.venv\\Scripts\\python.exe .\\scripts\\build_chatlog_knowledge_site.py --logs-root "C:\\Users\\Brian\\OneDrive\\Documents\\EVE\\logs" --since-date 2026-06-01
```
"""


SITE_CSS = r"""
:root {
  --bg: #f4f6f4;
  --panel: #ffffff;
  --ink: #1f2528;
  --muted: #687178;
  --line: #d9ded8;
  --green: #2f6f54;
  --blue: #285f91;
  --red: #b42318;
  --amber: #a15c00;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: "Segoe UI", Arial, sans-serif;
  letter-spacing: 0;
  width: 100%;
  overflow-x: hidden;
}
p, h1, h2, h3, h4, li, span, strong, div {
  max-width: 100%;
}
.site-header {
  min-height: 270px;
  padding: 34px 28px 28px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 22px;
  align-items: end;
  color: #fff;
  background:
    linear-gradient(rgba(12, 24, 27, 0.72), rgba(12, 24, 27, 0.78)),
    url("assets/hero.png");
  background-size: cover;
  background-position: center;
}
.eyebrow {
  margin: 0 0 8px;
  font-size: 13px;
  text-transform: uppercase;
  letter-spacing: 0;
  opacity: 0.88;
}
h1 {
  margin: 0;
  font-size: 42px;
  line-height: 1.08;
  overflow-wrap: anywhere;
}
.lede {
  margin: 14px 0 0;
  max-width: 760px;
  font-size: 17px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.stamp {
  min-width: 220px;
  padding: 14px;
  border: 1px solid rgba(255,255,255,0.35);
  border-radius: 8px;
  background: rgba(0,0,0,0.22);
}
.stamp span {
  display: block;
  font-size: 12px;
  opacity: 0.8;
}
.stamp strong {
  display: block;
  margin-top: 6px;
  font-size: 14px;
  overflow-wrap: anywhere;
}
main {
  padding: 20px;
}
.stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.stat, .notice, .toolbar, .topic, .instruction, .resource-table, .channel-list {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.stat {
  padding: 14px;
}
.stat strong {
  display: block;
  font-size: 25px;
  line-height: 1.1;
}
.stat span {
  display: block;
  margin-top: 6px;
  color: var(--muted);
  font-size: 12px;
}
.notice {
  padding: 13px 15px;
  color: #384348;
  margin-bottom: 16px;
  overflow-wrap: anywhere;
}
.toolbar {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 220px;
  gap: 12px;
  padding: 14px;
  margin-bottom: 24px;
}
label span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 5px;
}
input, select {
  width: 100%;
  min-height: 38px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 8px 10px;
  color: var(--ink);
  background: #fff;
}
.section-heading {
  margin: 20px 0 12px;
}
.section-heading h2 {
  margin: 0;
  font-size: 22px;
}
.section-heading p {
  margin: 6px 0 0;
  color: var(--muted);
  overflow-wrap: anywhere;
}
.topic-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}
.instruction-list {
  display: grid;
  gap: 14px;
}
.instruction {
  padding: 18px;
}
.instruction-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 12px;
  align-items: start;
}
.instruction h3 {
  margin: 0;
  font-size: 20px;
}
.instruction-summary {
  margin: 8px 0 0;
  line-height: 1.48;
  color: #354047;
}
.instruction-meta {
  color: var(--muted);
  font-size: 12px;
  margin-top: 8px;
}
.section-block {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--line);
}
.section-block h4 {
  margin: 0 0 8px;
  font-size: 15px;
}
.section-block ol,
.section-block ul {
  margin: 0;
  padding-left: 20px;
}
.section-block li {
  margin: 6px 0;
  line-height: 1.42;
}
.raw-toggle {
  min-height: 34px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #f8faf8;
  color: var(--ink);
  cursor: pointer;
  padding: 7px 10px;
  font-weight: 700;
}
.raw-text {
  display: none;
  margin: 12px 0 0;
  padding: 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: #fbfcfb;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  color: #30383c;
  font-size: 13px;
  line-height: 1.45;
}
.raw-text.open {
  display: block;
}
.topic {
  padding: 16px;
}
.topic h3 {
  margin: 0;
  font-size: 18px;
}
.tag {
  display: inline-flex;
  margin-bottom: 10px;
  padding: 4px 8px;
  border-radius: 999px;
  background: #e7f0ea;
  color: var(--green);
  font-size: 12px;
  font-weight: 700;
}
.topic p {
  margin: 10px 0;
  line-height: 1.48;
}
.topic ul {
  margin: 10px 0 0;
  padding-left: 18px;
}
.topic li {
  margin: 6px 0;
}
.source {
  color: var(--muted);
  font-size: 12px;
  margin-top: 12px;
}
.resource-table {
  overflow: hidden;
}
.resource {
  display: grid;
  grid-template-columns: minmax(220px, 1.2fr) 160px 130px 90px minmax(180px, 1fr);
  gap: 12px;
  align-items: center;
  padding: 11px 14px;
  border-bottom: 1px solid var(--line);
}
.resource:last-child {
  border-bottom: 0;
}
.resource a {
  color: var(--blue);
  font-weight: 700;
  text-decoration: none;
  overflow-wrap: anywhere;
}
.resource-name {
  font-weight: 700;
  overflow-wrap: anywhere;
}
.muted {
  color: var(--muted);
  font-size: 12px;
  overflow-wrap: anywhere;
}
.pill {
  justify-self: start;
  padding: 4px 8px;
  border-radius: 999px;
  color: #fff;
  background: var(--green);
  font-size: 12px;
  font-weight: 700;
  text-transform: uppercase;
}
.pill.redacted {
  background: var(--amber);
}
.channel-list {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 0;
  overflow: hidden;
}
.channel {
  padding: 12px 14px;
  border-right: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
}
.channel strong {
  display: block;
}
@media (max-width: 960px) {
  .site-header {
    grid-template-columns: 1fr;
  }
  .stats {
    grid-template-columns: repeat(3, minmax(0, 1fr));
  }
  .topic-grid {
    grid-template-columns: 1fr;
  }
  .instruction-header {
    grid-template-columns: 1fr;
  }
  .resource {
    grid-template-columns: 1fr;
  }
  .channel-list {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}
@media (max-width: 620px) {
  .site-header {
    display: flex;
    flex-direction: column;
    align-items: stretch;
    width: 100vw;
    max-width: 100vw;
    overflow: hidden;
    padding: 24px 18px;
  }
  .site-header * {
    min-width: 0;
  }
  .site-header > div {
    min-width: 0;
    max-width: 100%;
  }
  .stamp {
    min-width: 0;
    width: 100%;
    max-width: calc(100vw - 36px);
  }
  h1 {
    font-size: 32px;
    max-width: 13ch;
  }
  .lede {
    max-width: 34ch;
    font-size: 16px;
  }
  main {
    padding: 14px;
    max-width: 100vw;
    overflow: hidden;
  }
  .stats, .toolbar, .channel-list {
    grid-template-columns: 1fr;
  }
}
"""


SITE_JS = r"""
const data = JSON.parse(document.getElementById("knowledge-data").textContent);
const state = { query: "", status: "all" };

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[char]));
}

function compactDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function matchesText(item) {
  if (!state.query) return true;
  const text = JSON.stringify(item).toLowerCase();
  return text.includes(state.query);
}

function renderStats() {
  const stats = data.stats;
  document.getElementById("window-range").textContent =
    `${compactDate(data.meta.window_start)} to ${compactDate(data.meta.window_end)}`;
  document.getElementById("privacy-note").textContent = data.meta.privacy_note;
  const items = [
    ["Files", stats.file_count],
    ["Messages", stats.message_count],
    ["Channels", stats.channel_count],
    ["Instructions", stats.instruction_count],
    ["MOTD blocks", stats.motd_count],
    ["Public links", stats.public_link_count],
    ["Review links", stats.review_link_count]
  ];
  document.getElementById("stats").innerHTML = items.map(([label, value]) => `
    <div class="stat"><strong>${escapeHtml(value)}</strong><span>${escapeHtml(label)}</span></div>
  `).join("");
}

function renderTopics() {
  const topics = data.topics.filter(matchesText);
  document.getElementById("topics").innerHTML = topics.map(topic => `
    <article class="topic">
      <span class="tag">${escapeHtml(topic.category)}</span>
      <h3>${escapeHtml(topic.title)}</h3>
      <p>${escapeHtml(topic.summary)}</p>
      <ul>${topic.details.map(detail => `<li>${escapeHtml(detail)}</li>`).join("")}</ul>
      <div class="source">${escapeHtml(topic.sources.map(source =>
        `${source.channel}: ${source.message_count} msgs, ${source.motd_blocks} MOTDs`
      ).join(" | "))}</div>
    </article>
  `).join("") || `<div class="topic">No topics match the current search.</div>`;
}

function renderInstructions() {
  const instructions = data.instructions.filter(matchesText);
  document.getElementById("instructions").innerHTML = instructions.map(item => {
    const sections = item.sections.map(section => `
      <div class="section-block">
        <h4>${escapeHtml(section.heading)}</h4>
        <ul>${section.items.map(detail => `<li>${escapeHtml(detail)}</li>`).join("")}</ul>
      </div>
    `).join("");
    const source = item.seen_range
      ? `${item.channel} - ${item.seen_range.message_count} msgs - latest MOTD ${compactDate(item.seen_at)}`
      : `${item.channel} - latest MOTD ${compactDate(item.seen_at)}`;
    return `
      <article class="instruction">
        <div class="instruction-header">
          <div>
            <span class="tag">${escapeHtml(item.source)}</span>
            <h3>${escapeHtml(item.title)}</h3>
            <p class="instruction-summary">${escapeHtml(item.summary)}</p>
            <div class="instruction-meta">${escapeHtml(source)}</div>
          </div>
          <button class="raw-toggle" type="button" data-raw="${escapeHtml(item.id)}">Raw MOTD</button>
        </div>
        ${sections}
        <pre class="raw-text" id="raw-${escapeHtml(item.id)}">${escapeHtml(item.raw_reproduction)}</pre>
      </article>
    `;
  }).join("") || `<div class="instruction">No instructions match the current search.</div>`;

  document.querySelectorAll("[data-raw]").forEach(button => {
    button.addEventListener("click", () => {
      const target = document.getElementById(`raw-${button.dataset.raw}`);
      if (target) target.classList.toggle("open");
    });
  });
}

function renderResources() {
  const resources = data.resources.filter(item => {
    if (state.status !== "all" && item.status !== state.status) return false;
    return matchesText(item);
  });
  document.getElementById("resources").innerHTML = resources.map(item => {
    const title = item.status === "public"
      ? `<a href="${escapeHtml(item.url)}" target="_blank" rel="noreferrer">${escapeHtml(item.label)}</a>`
      : `<span class="resource-name">${escapeHtml(item.label)}</span>`;
    return `
      <div class="resource">
        <div>${title}<div class="muted">${escapeHtml(item.display_url)}</div></div>
        <div>${escapeHtml(item.category)}</div>
        <span class="pill ${escapeHtml(item.status)}">${escapeHtml(item.status)}</span>
        <div class="muted">${escapeHtml(item.count)} seen</div>
        <div class="muted">${escapeHtml(item.channels.join(", "))}</div>
      </div>
    `;
  }).join("") || `<div class="resource"><div>No resources match the current filters.</div></div>`;
}

function renderChannels() {
  const channels = data.channels.filter(matchesText);
  document.getElementById("channels").innerHTML = channels.map(channel => `
    <div class="channel">
      <strong>${escapeHtml(channel.name)}</strong>
      <span class="muted">${escapeHtml(channel.message_count)} messages</span>
    </div>
  `).join("");
}

function renderAll() {
  renderStats();
  renderInstructions();
  renderTopics();
  renderResources();
  renderChannels();
}

document.getElementById("search").addEventListener("input", event => {
  state.query = event.target.value.trim().toLowerCase();
  renderAll();
});
document.getElementById("status-filter").addEventListener("change", event => {
  state.status = event.target.value;
  renderResources();
});
renderAll();
"""


if __name__ == "__main__":
    raise SystemExit(main())
