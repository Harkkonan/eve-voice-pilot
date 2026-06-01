from __future__ import annotations

import csv
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "data" / "eve_shortcuts_catalog.csv"
HOLD_SECONDS = 0.10
AURA_SUFFIX = "Aura"
NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
NUMBER_WORDS = {
    1: "one",
    2: "two",
    3: "three",
    4: "four",
    5: "five",
    6: "six",
    7: "seven",
    8: "eight",
}
GROUP_ORDER = [
    "App Control",
    "Emergency and Survival",
    "Ship Movement",
    "Drones",
    "Scanning and Intel",
    "Targeting",
    "Modules",
    "Windows",
    "Fleet",
]
PRIORITY_ORDER = ["App", "Critical", "High", "Medium", "Low", "Rare"]
SHORTCUT_ALIASES = {
    "\\": "BACKSLASH",
    "ALT": "ALT",
    "CTRL": "CTRL",
    "CONTROL": "CTRL",
    "SHIFT": "SHIFT",
    "ESC": "ESC",
    "ESCAPE": "ESC",
    "ENTER": "ENTER",
    "RETURN": "ENTER",
    "SPACE": "SPACE",
    "TAB": "TAB",
    "BACKSPACE": "BACKSPACE",
    "PAGE UP": "PAGE UP",
    "PAGE DOWN": "PAGE DOWN",
    "SYS REQ": "PRINTSCREEN",
    "SYSREQ": "PRINTSCREEN",
    "PRINT SCREEN": "PRINTSCREEN",
    "PRINTSCREEN": "PRINTSCREEN",
    "MOUSE4": "MOUSE4",
    "MOUSE5": "MOUSE5",
}


def aura_response(text: str) -> dict:
    return {
        "response_suffix": AURA_SUFFIX,
        "response_text": text,
    }


def numbered_phrases(*prefixes: str, index: int) -> str:
    number = NUMBER_WORDS[index]
    phrases = []
    for prefix in prefixes:
        phrases.append(f"{prefix} {index}")
        phrases.append(f"{prefix} {number}")
    return "|".join(phrases)


def normalize_name(value: str) -> str:
    return " ".join(NORMALIZE_RE.sub(" ", value.lower()).split())


def normalize_shortcut(shortcut: str) -> str:
    value = shortcut.strip()
    if not value or value == "(None)":
        return ""

    upper = value.upper()
    if upper.startswith("NUM "):
        suffix = upper[4:].strip()
        if suffix == "+":
            return "NUMPLUS"
        if suffix == "-":
            return "NUMMINUS"
        if suffix.isdigit():
            return f"NUM{suffix}"

    if upper in SHORTCUT_ALIASES:
        return SHORTCUT_ALIASES[upper]

    parts = [part.strip() for part in value.split("-") if part.strip()]
    return "+".join(normalize_shortcut_part(part) for part in parts)


def normalize_shortcut_part(part: str) -> str:
    upper = re.sub(r"\s+", " ", part.strip().upper())
    return SHORTCUT_ALIASES.get(upper, upper)


def load_catalog_rows() -> list[dict]:
    with CATALOG_PATH.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def add_catalog_rows() -> None:
    existing = {normalize_name(row["eve_command"]) for row in STANDARD_ROWS}
    for record in load_catalog_rows():
        shortcut = normalize_shortcut(record.get("shortcut", ""))
        if not shortcut:
            continue

        eve_command = record["command"].strip()
        normalized_command = normalize_name(eve_command)
        if normalized_command in existing:
            continue
        existing.add(normalized_command)

        phrase = record.get("voice_phrase_suggestion", "").strip() or eve_command.lower()
        action = "Verify this shortcut in EVE."
        medium_overheat = re.fullmatch(r"Toggle Overload on Medium Power Slot ([1-8])", eve_command)
        if medium_overheat:
            shortcut = f"ALT+SHIFT+{medium_overheat.group(1)}"
            action = "Change in EVE. This avoids the Alt-F4 style overheat pattern."
        row = {
            "priority": record.get("frequency_tier", "Low").strip() or "Low",
            "group": record.get("function_group", "Other").strip() or "Other",
            "eve_category": record.get("game_category", "Unknown").strip() or "Unknown",
            "eve_command": eve_command,
            "standard_shortcut": shortcut,
            "voice_phrases": phrase,
            "action": action,
        }
        if eve_command == "Autopilot":
            row["voice_phrases"] = "autopilot|toggle autopilot|auto pilot"
            row.update(aura_response("I have the helm."))
        STANDARD_ROWS.append(row)


STANDARD_ROWS = [
    {
        "priority": "App",
        "group": "App Control",
        "eve_category": "EVE Voice Pilot",
        "eve_command": "Arm/Pause Listening",
        "standard_shortcut": "PAUSE",
        "voice_phrases": "",
        "action": "Set this in EVE Voice Pilot, not in EVE.",
        "profile": False,
    },
    {
        "priority": "Critical",
        "group": "Emergency and Survival",
        "eve_category": "Drones",
        "eve_command": "All Drones: Return to Drone Bay",
        "standard_shortcut": "SHIFT+R",
        "voice_phrases": "recall drones|return drones|drones home",
        "action": "Keep default.",
        **aura_response("Drones returning."),
    },
    {
        "priority": "Critical",
        "group": "Emergency and Survival",
        "eve_category": "Navigation",
        "eve_command": "Stop Ship",
        "standard_shortcut": "CTRL+SPACE",
        "voice_phrases": "stop ship|full stop|stop the ship",
        "action": "Keep default.",
        **aura_response("Full stop."),
    },
    {
        "priority": "Critical",
        "group": "Emergency and Survival",
        "eve_category": "Combat",
        "eve_command": "Warp to",
        "standard_shortcut": "S",
        "voice_phrases": "warp|warp to|warp now",
        "action": "Keep default.",
        **aura_response("Warp command sent."),
    },
    {
        "priority": "Critical",
        "group": "Emergency and Survival",
        "eve_category": "Combat",
        "eve_command": "Dock/Jump/Activate gate",
        "standard_shortcut": "D",
        "voice_phrases": "jump|dock|activate gate|jump gate",
        "action": "Keep default.",
        **aura_response("Gate command sent."),
    },
    {
        "priority": "High",
        "group": "Ship Movement",
        "eve_category": "Combat",
        "eve_command": "Align to",
        "standard_shortcut": "A",
        "voice_phrases": "align|align to",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Ship Movement",
        "eve_category": "Combat",
        "eve_command": "Approach",
        "standard_shortcut": "Q",
        "voice_phrases": "approach|approach target",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Ship Movement",
        "eve_category": "Combat",
        "eve_command": "Orbit",
        "standard_shortcut": "W",
        "voice_phrases": "orbit|orbit target",
        "action": "Keep default.",
        "press_count": 2,
        "repeat_gap_seconds": 0.10,
    },
    {
        "priority": "High",
        "group": "Ship Movement",
        "eve_category": "Combat",
        "eve_command": "Keep at Range",
        "standard_shortcut": "E",
        "voice_phrases": "keep range|keep at range",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Ship Movement",
        "eve_category": "Navigation",
        "eve_command": "Set Full Speed",
        "standard_shortcut": "CTRL+ALT+SPACE",
        "voice_phrases": "full speed|go full speed",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Drones",
        "eve_category": "Drones",
        "eve_command": "All Drones: Engage",
        "standard_shortcut": "F",
        "voice_phrases": "drones engage|engage drones",
        "action": "Keep default.",
        **aura_response("Drones engaging."),
    },
    {
        "priority": "High",
        "group": "Drones",
        "eve_category": "Drones",
        "eve_command": "All Drones: Return and Orbit",
        "standard_shortcut": "SHIFT+ALT+R",
        "voice_phrases": "drones orbit|return and orbit",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Drones",
        "eve_category": "Drones",
        "eve_command": "Launch Drones",
        "standard_shortcut": "SHIFT+F",
        "voice_phrases": "launch drones|send drones",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Scanning and Intel",
        "eve_category": "Combat",
        "eve_command": "Directional Scan",
        "standard_shortcut": "V",
        "voice_phrases": "d scan|directional scan",
        "action": "Keep default.",
        **aura_response("Directional scan pulsed."),
    },
    {
        "priority": "High",
        "group": "Scanning and Intel",
        "eve_category": "Window",
        "eve_command": "Directional Scanner",
        "standard_shortcut": "ALT+D",
        "voice_phrases": "open d scan|open directional scanner",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Scanning and Intel",
        "eve_category": "Window",
        "eve_command": "Probe Scanner",
        "standard_shortcut": "ALT+P",
        "voice_phrases": "open probes|open probe scanner",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Scanning and Intel",
        "eve_category": "Combat",
        "eve_command": "Refresh Probe Scan",
        "standard_shortcut": "B",
        "voice_phrases": "refresh probes|probe scan",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Scanning and Intel",
        "eve_category": "Window",
        "eve_command": "Map",
        "standard_shortcut": "F10",
        "voice_phrases": "open map|map",
        "action": "Keep default.",
        **aura_response("Map open."),
    },
    {
        "priority": "Medium",
        "group": "Scanning and Intel",
        "eve_category": "Window",
        "eve_command": "Solar System Map",
        "standard_shortcut": "F9",
        "voice_phrases": "open system map|system map",
        "action": "Keep default. EVE Voice Pilot uses PAUSE, so F9 is free for EVE.",
    },
    {
        "priority": "High",
        "group": "Targeting",
        "eve_category": "Combat",
        "eve_command": "Lock target",
        "standard_shortcut": "CTRL",
        "voice_phrases": "lock target|lock",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Targeting",
        "eve_category": "Combat",
        "eve_command": "Unlock target",
        "standard_shortcut": "CTRL+SHIFT",
        "voice_phrases": "unlock target|unlock",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Targeting",
        "eve_category": "Navigation",
        "eve_command": "Select next target",
        "standard_shortcut": "ALT+RIGHT",
        "voice_phrases": "next target|select next target",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Targeting",
        "eve_category": "Navigation",
        "eve_command": "Select previous target",
        "standard_shortcut": "ALT+LEFT",
        "voice_phrases": "previous target|select previous target",
        "action": "Keep default.",
    },
    {
        "priority": "Medium",
        "group": "Targeting",
        "eve_category": "Combat",
        "eve_command": "Track",
        "standard_shortcut": "C",
        "voice_phrases": "track|track target",
        "action": "Keep default.",
    },
    {
        "priority": "Medium",
        "group": "Targeting",
        "eve_category": "Combat",
        "eve_command": "Show info",
        "standard_shortcut": "T",
        "voice_phrases": "show info|target info",
        "action": "Keep default.",
    },
    {
        "priority": "Medium",
        "group": "Targeting",
        "eve_category": "Navigation",
        "eve_command": "Tactical Overlay",
        "standard_shortcut": "CTRL+D",
        "voice_phrases": "tactical overlay|toggle tactical overlay",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Modules",
        "eve_category": "Modules",
        "eve_command": "Reload Ammo",
        "standard_shortcut": "CTRL+R",
        "voice_phrases": "reload|reload ammo",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Inventory",
        "standard_shortcut": "ALT+C",
        "voice_phrases": "open inventory|inventory",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Fitting",
        "standard_shortcut": "ALT+F",
        "voice_phrases": "open fitting|fitting",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Regional Market",
        "standard_shortcut": "ALT+R",
        "voice_phrases": "open market|market",
        "action": "Keep default.",
    },
    {
        "priority": "Low",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Contracts",
        "standard_shortcut": "CTRL+ALT+C",
        "voice_phrases": "open contracts|contracts",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "Low",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Open Drone Bay Of Active Ship",
        "standard_shortcut": "ALT+SHIFT+D",
        "voice_phrases": "open drone bay|drone bay",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "Low",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Open Fighter Bay Of Active Ship",
        "standard_shortcut": "ALT+SHIFT+F",
        "voice_phrases": "open fighter bay|fighter bay",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "Medium",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Wallet",
        "standard_shortcut": "ALT+W",
        "voice_phrases": "open wallet|wallet",
        "action": "Keep default.",
    },
    {
        "priority": "Medium",
        "group": "Windows",
        "eve_category": "Window",
        "eve_command": "Skills",
        "standard_shortcut": "ALT+X",
        "voice_phrases": "open skills|skills",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Target (Selected)",
        "standard_shortcut": "X",
        "voice_phrases": "broadcast target|fleet target",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Spotted an Enemy",
        "standard_shortcut": "Z",
        "voice_phrases": "enemy spotted|broadcast enemy",
        "action": "Keep default.",
    },
    {
        "priority": "High",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Need Shield",
        "standard_shortcut": "CTRL+ALT+1",
        "voice_phrases": "need shield|broadcast need shield",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "High",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Need Armor",
        "standard_shortcut": "CTRL+ALT+2",
        "voice_phrases": "need armor|broadcast need armor",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "High",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Need Capacitor",
        "standard_shortcut": "CTRL+ALT+3",
        "voice_phrases": "need cap|need capacitor|broadcast need capacitor",
        "action": "Add this shortcut in EVE.",
    },
    {
        "priority": "Medium",
        "group": "Fleet",
        "eve_category": "Navigation",
        "eve_command": "Broadcast: Need Backup",
        "standard_shortcut": "CTRL+ALT+4",
        "voice_phrases": "need backup|broadcast need backup",
        "action": "Add this shortcut in EVE.",
    },
]


def add_module_rows() -> None:
    for index in range(1, 9):
        STANDARD_ROWS.append({
            "priority": "High",
            "group": "Modules",
            "eve_category": "Modules",
            "eve_command": f"Activate High Power Slot {index}",
            "standard_shortcut": f"F{index}",
            "voice_phrases": numbered_phrases("module", "high", "fire", index=index),
            "action": "Keep default.",
        })
    for index in range(1, 9):
        STANDARD_ROWS.append({
            "priority": "High",
            "group": "Modules",
            "eve_category": "Modules",
            "eve_command": f"Activate Medium Power Slot {index}",
            "standard_shortcut": f"ALT+{index}",
            "voice_phrases": numbered_phrases("mid", "medium", index=index),
            "action": "Change in EVE. This avoids the default Alt-F4 style medium-slot pattern.",
        })
    for index in range(1, 9):
        STANDARD_ROWS.append({
            "priority": "High",
            "group": "Modules",
            "eve_category": "Modules",
            "eve_command": f"Activate Low Power Slot {index}",
            "standard_shortcut": f"CTRL+F{index}",
            "voice_phrases": numbered_phrases("low", index=index),
            "action": "Keep default.",
        })


def profile_name(command: str) -> str:
    return command.replace("Broadcast: ", "Broadcast ")


def profile_commands() -> list[dict]:
    commands = []
    for row in ordered_rows():
        if row.get("profile") is False:
            continue
        phrases = [phrase.strip() for phrase in row["voice_phrases"].split("|") if phrase.strip()]
        if not phrases:
            continue
        command = {
            "name": profile_name(row["eve_command"]),
            "phrases": phrases,
            "key": row["standard_shortcut"],
            "hold_seconds": HOLD_SECONDS,
        }
        if int(row.get("press_count", 1)) > 1:
            command["press_count"] = int(row["press_count"])
            command["repeat_gap_seconds"] = float(row.get("repeat_gap_seconds", 0.10))
        if row.get("response_suffix"):
            command["response_suffix"] = row["response_suffix"]
        if row.get("response_text"):
            command["response_text"] = row["response_text"]
        commands.append(command)
    return commands


def write_csv(path: Path) -> None:
    fieldnames = [
        "priority",
        "group",
        "eve_category",
        "eve_command",
        "standard_shortcut",
        "voice_phrases",
        "press_count",
        "repeat_gap_seconds",
        "response_suffix",
        "response_text",
        "action",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in ordered_rows():
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def ordered_rows() -> list[dict]:
    order = {group: index for index, group in enumerate(GROUP_ORDER)}
    return sorted(
        STANDARD_ROWS,
        key=lambda row: (
            order.get(row["group"], len(order)),
            PRIORITY_ORDER.index(row["priority"])
            if row["priority"] in PRIORITY_ORDER
            else 99,
            row["eve_command"],
        ),
    )


def write_profile(path: Path) -> None:
    data = {
        "name": "EVE Voice Pilot Standard",
        "commands": profile_commands(),
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_docs(path: Path) -> None:
    lines = [
        "# EVE Voice Pilot Keybind Standard",
        "",
        "Use this as the matching set between EVE Online and EVE Voice Pilot.",
        "",
        "Principles:",
        "",
        "- Keep EVE defaults where they are already good.",
        "- Use `PAUSE` for EVE Voice Pilot arm/pause, so EVE keeps `F9` for Solar System Map.",
        "- Remap medium slots to `Alt+1` through `Alt+8` to avoid the risky default `Alt+F4` pattern.",
        "- Include every catalog row that has a real shortcut, with explicit phrases for risky commands.",
        "",
        "Enter or verify the rows below in EVE Settings > Shortcuts. Rows marked `Add this shortcut in EVE` or `Change in EVE` are the important manual edits.",
        "",
    ]
    rows = ordered_rows()
    extra_groups = sorted({row["group"] for row in rows if row["group"] not in GROUP_ORDER})
    for group in [*GROUP_ORDER, *extra_groups]:
        group_rows = [row for row in rows if row["group"] == group]
        if not group_rows:
            continue
        lines.extend([
            f"## {group}",
            "",
            "| Priority | EVE category | EVE command | Standard shortcut | Voice phrases | Presses | Voice response | Action |",
            "|---|---|---|---|---|---|---|---|",
        ])
        for row in group_rows:
            voice_phrases = row["voice_phrases"].replace("|", ", ")
            presses = ""
            if int(row.get("press_count", 1)) > 1:
                presses = f"{int(row['press_count'])}x, {float(row.get('repeat_gap_seconds', 0.10)):.2f}s gap"
            response = ""
            if row.get("response_suffix"):
                response = f"{row['response_suffix']}: {row.get('response_text', '')}".strip()
            lines.append(
                f"| {row['priority']} | {row['eve_category']} | {row['eve_command']} | "
                f"`{row['standard_shortcut']}` | {voice_phrases} | {presses} | {response} | {row['action']} |"
            )
        lines.append(
            ""
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    add_module_rows()
    add_catalog_rows()
    data_dir = ROOT / "data"
    docs_dir = ROOT / "docs"
    profiles_dir = ROOT / "profiles"
    data_dir.mkdir(exist_ok=True)
    docs_dir.mkdir(exist_ok=True)
    profiles_dir.mkdir(exist_ok=True)

    write_csv(data_dir / "eve_voice_keybind_standard.csv")
    write_docs(docs_dir / "eve_voice_keybind_standard.md")
    write_profile(profiles_dir / "eve_voice_standard.json")
    write_profile(profiles_dir / "eve_sample.json")
    print(f"Wrote {len(STANDARD_ROWS)} standard rows and {len(profile_commands())} voice commands.")


if __name__ == "__main__":
    main()
