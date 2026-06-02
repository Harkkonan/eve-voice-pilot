from __future__ import annotations

import argparse
from dataclasses import dataclass
import re
import sys
import time
from typing import Callable

from .input_sender import active_window_title, parse_key_chord, send_key_chord


SPACE_RE = re.compile(r"\s+")
REGION_SPLIT_RE = re.compile(r"[\s,]+")
DEFAULT_POLL_SECONDS = 0.50
DEFAULT_HOLD_SECONDS = 0.06
DEFAULT_STABLE_SAMPLES = 2
DEFAULT_WINDOW_TITLE = "EVE"


class OcrWatcherError(RuntimeError):
    pass


@dataclass(frozen=True)
class ScreenRegion:
    left: int
    top: int
    width: int
    height: int

    def as_mss_monitor(self) -> dict[str, int]:
        return {
            "left": self.left,
            "top": self.top,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True)
class ValueEvent:
    value: str
    previous: str | None
    initial: bool
    stable_samples: int

    @property
    def changed(self) -> bool:
        return self.previous is not None and self.value != self.previous


class StableValueTracker:
    def __init__(self, stable_samples: int = DEFAULT_STABLE_SAMPLES):
        if stable_samples < 1:
            raise ValueError("stable_samples must be at least 1")
        self.stable_samples = stable_samples
        self._candidate = ""
        self._candidate_count = 0
        self._current: str | None = None

    @property
    def current(self) -> str | None:
        return self._current

    def observe(self, value: str) -> ValueEvent | None:
        value = value.strip()
        if not value:
            return None

        if value == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = value
            self._candidate_count = 1

        if self._candidate_count < self.stable_samples:
            return None

        if self._current is None:
            self._current = value
            return ValueEvent(value=value, previous=None, initial=True, stable_samples=self._candidate_count)

        if value == self._current:
            return None

        previous = self._current
        self._current = value
        return ValueEvent(value=value, previous=previous, initial=False, stable_samples=self._candidate_count)


def parse_region(value: str) -> ScreenRegion:
    parts = [part for part in REGION_SPLIT_RE.split(value.strip()) if part]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("Region must be four numbers: left,top,width,height.")
    try:
        left, top, width, height = (int(part) for part in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Region must use whole-number pixels.") from exc
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("Region width and height must be positive.")
    return ScreenRegion(left=left, top=top, width=width, height=height)


def normalize_ocr_value(raw_text: str, *, pattern: str | None = None, keep_case: bool = False) -> str:
    lines = [SPACE_RE.sub(" ", line).strip() for line in raw_text.splitlines()]
    value = " ".join(line for line in lines if line)
    if pattern:
        match = re.search(pattern, value)
        if not match:
            return ""
        value = match.group(1) if match.groups() else match.group(0)
    value = SPACE_RE.sub(" ", value).strip()
    if not keep_case:
        value = value.casefold()
    return value


def build_tesseract_config(psm: int, extra_config: str = "") -> str:
    parts = [f"--psm {psm}"]
    if extra_config.strip():
        parts.append(extra_config.strip())
    return " ".join(parts)


def capture_region(region: ScreenRegion):
    try:
        import mss
        from PIL import Image
    except ImportError as exc:
        raise OcrWatcherError(
            "Screen capture needs the mss and Pillow packages. Run scripts\\setup.ps1 or install requirements.txt."
        ) from exc

    with mss.mss() as screen:
        shot = screen.grab(region.as_mss_monitor())
        return Image.frombytes("RGB", shot.size, shot.rgb)


def prepare_ocr_image(image, *, scale: float = 2.0, grayscale: bool = True, autocontrast: bool = True):
    try:
        from PIL import ImageOps
    except ImportError as exc:
        raise OcrWatcherError("Image preparation needs Pillow. Run scripts\\setup.ps1 or install requirements.txt.") from exc

    if grayscale:
        image = ImageOps.grayscale(image)
    if autocontrast:
        image = ImageOps.autocontrast(image)
    if scale != 1.0:
        width = max(1, int(image.width * scale))
        height = max(1, int(image.height * scale))
        image = image.resize((width, height))
    return image


def read_ocr_text(
    region: ScreenRegion,
    *,
    lang: str = "eng",
    psm: int = 7,
    scale: float = 2.0,
    tesseract_cmd: str = "",
    tesseract_config: str = "",
) -> str:
    try:
        import pytesseract
    except ImportError as exc:
        raise OcrWatcherError(
            "OCR needs pytesseract. Run scripts\\setup.ps1 or install requirements.txt."
        ) from exc

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd

    image = prepare_ocr_image(capture_region(region), scale=scale)
    try:
        return pytesseract.image_to_string(
            image,
            lang=lang,
            config=build_tesseract_config(psm, tesseract_config),
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise OcrWatcherError(
            "Tesseract OCR is not installed or is not on PATH. Install Tesseract, or pass --tesseract-cmd."
        ) from exc


def window_allows_send(expected_title: str) -> tuple[bool, str]:
    if not expected_title:
        return True, ""
    title = active_window_title()
    return expected_title.casefold() in title.casefold(), title


def maybe_send_hotkey(
    hotkey: str,
    *,
    hold_seconds: float,
    dry_run: bool,
    expected_window_title: str,
    log: Callable[[str], None] = print,
) -> bool:
    allowed, title = window_allows_send(expected_window_title)
    if not allowed:
        log(f"Blocked: active window title is {title!r}; expected text {expected_window_title!r}.")
        return False
    if dry_run:
        log(f"Dry run: would send {hotkey}.")
        return True
    send_key_chord(hotkey, press_seconds=hold_seconds)
    log(f"Sent {hotkey}.")
    return True


def run_once(args: argparse.Namespace) -> int:
    raw_text = read_ocr_text(
        args.region,
        lang=args.lang,
        psm=args.psm,
        scale=args.scale,
        tesseract_cmd=args.tesseract_cmd,
        tesseract_config=args.tesseract_config,
    )
    value = normalize_ocr_value(raw_text, pattern=args.pattern, keep_case=args.keep_case)
    print(f"Raw OCR: {raw_text.strip()!r}")
    print(f"Watched value: {value!r}")
    return 0


def run_watch(args: argparse.Namespace) -> int:
    tracker = StableValueTracker(args.stable_samples)
    last_fire_at = 0.0

    print(
        "Watching "
        f"{args.region.left},{args.region.top},{args.region.width},{args.region.height}; "
        f"hotkey={args.hotkey}; poll={args.poll:.2f}s; stable={args.stable_samples}."
    )
    if args.window_title_contains:
        print(f"Key sending is allowed only when the active window title contains {args.window_title_contains!r}.")
    if args.dry_run:
        print("Dry run is on. Changes will be logged but no hotkey will be sent.")

    try:
        while True:
            raw_text = read_ocr_text(
                args.region,
                lang=args.lang,
                psm=args.psm,
                scale=args.scale,
                tesseract_cmd=args.tesseract_cmd,
                tesseract_config=args.tesseract_config,
            )
            value = normalize_ocr_value(raw_text, pattern=args.pattern, keep_case=args.keep_case)
            event = tracker.observe(value)

            if args.verbose:
                label = value or "<empty>"
                print(f"{_timestamp()} sample: {label}")

            if event and event.initial:
                print(f"{_timestamp()} baseline: {event.value}")
            elif event and event.changed:
                print(f"{_timestamp()} changed: {event.previous} -> {event.value}")
                now = time.monotonic()
                if args.cooldown > 0 and now - last_fire_at < args.cooldown:
                    print(f"Cooldown: skipped send because the last send was {now - last_fire_at:.2f}s ago.")
                elif maybe_send_hotkey(
                    args.hotkey,
                    hold_seconds=args.hold_seconds,
                    dry_run=args.dry_run,
                    expected_window_title=args.window_title_contains,
                ):
                    last_fire_at = now

            time.sleep(args.poll)
    except KeyboardInterrupt:
        print("Stopped.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Watch a screen region with OCR and send a Windows key chord when the watched value changes.",
    )
    parser.add_argument(
        "--region",
        required=True,
        type=parse_region,
        help="Screen region in pixels: left,top,width,height. Negative left/top are allowed for extra monitors.",
    )
    parser.add_argument("--hotkey", required=True, help="Key chord to send on change, like F1 or CTRL+SHIFT+F9.")
    parser.add_argument("--pattern", help="Optional regex used to extract the watched value from OCR text.")
    parser.add_argument("--poll", type=_positive_float, default=DEFAULT_POLL_SECONDS, help="Seconds between reads.")
    parser.add_argument(
        "--stable-samples",
        type=_positive_int,
        default=DEFAULT_STABLE_SAMPLES,
        help="Matching OCR reads required before accepting a value.",
    )
    parser.add_argument("--cooldown", type=_non_negative_float, default=0.0, help="Minimum seconds between sends.")
    parser.add_argument("--hold-seconds", type=_positive_float, default=DEFAULT_HOLD_SECONDS, help="Hotkey hold time.")
    parser.add_argument("--lang", default="eng", help="Tesseract language, default eng.")
    parser.add_argument("--psm", type=_positive_int, default=7, help="Tesseract page segmentation mode.")
    parser.add_argument("--scale", type=_positive_float, default=2.0, help="Image scale before OCR.")
    parser.add_argument("--tesseract-cmd", default="", help="Full path to tesseract.exe if it is not on PATH.")
    parser.add_argument("--tesseract-config", default="", help="Extra Tesseract config flags.")
    parser.add_argument(
        "--window-title-contains",
        default=DEFAULT_WINDOW_TITLE,
        help="Only send keys when the active window title contains this text. Use an empty string to disable.",
    )
    parser.add_argument("--keep-case", action="store_true", help="Compare OCR values with case preserved.")
    parser.add_argument("--dry-run", action="store_true", help="Log changes without sending the hotkey.")
    parser.add_argument("--once", action="store_true", help="Read the region once, print the OCR result, and exit.")
    parser.add_argument("--verbose", action="store_true", help="Print every accepted OCR sample.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        parse_key_chord(args.hotkey)
        if args.once:
            return run_once(args)
        return run_watch(args)
    except (OcrWatcherError, OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _timestamp() -> str:
    return time.strftime("%H:%M:%S")


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a whole number.") from exc
    if number < 1:
        raise argparse.ArgumentTypeError("Value must be at least 1.")
    return number


def _positive_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a number.") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("Value must be greater than 0.")
    return number


def _non_negative_float(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a number.") from exc
    if number < 0:
        raise argparse.ArgumentTypeError("Value must be 0 or greater.")
    return number


if __name__ == "__main__":
    raise SystemExit(main())
