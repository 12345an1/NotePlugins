#!/usr/bin/env python3
"""Extract best-effort text records from legacy OLE/CFB .ppt files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import struct
import sys

try:
    from .safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text
except ImportError:
    from safe_io import ensure_safe_input_file, read_bytes_no_follow, safe_write_text


END_OF_CHAIN = -2
FREE_SECTOR = -1
NO_STREAM = -1
MAX_LEGACY_PPT_INPUT_BYTES = 256 * 1024 * 1024
CFB_HEADER_BYTES = 512
VALID_SECTOR_SHIFTS = {9, 12}
VALID_MINI_SECTOR_SHIFT = 6
MAX_PPT_RECORD_NESTING = 64
MAX_PPT_RECORDS = 1_000_000

PLACEHOLDER_RE = re.compile(
    r"^(?:"
    r"\d+|"
    r"幻灯片\s*\d+|"
    r"单击此处编辑.*|"
    r"Click to edit.*|"
    r"Microsoft PowerPoint.*"
    r")$",
    re.I,
)


@dataclass(frozen=True)
class LegacyPptTextResult:
    source: Path
    text: str
    text_record_count: int

    @property
    def line_count(self) -> int:
        return sum(1 for line in self.text.splitlines() if line.strip())

    @property
    def markdown(self) -> str:
        lines = [
            f"# Legacy PPT Text Fallback: {self.source.name}",
            "",
            "- Backend: `ole-cfb-text-records`",
            f"- Text records: {self.text_record_count}",
            f"- Extracted text lines: {self.line_count}",
            "- Coverage: partial/fallback extraction; use as text hints, not complete slide coverage.",
            "",
        ]
        if self.text.strip():
            lines += ["## Extracted Text Records", "", self.text.rstrip(), ""]
        else:
            lines += [
                "[No extractable text records; the legacy PPT may be image-only, encrypted, compressed in an unsupported way, or OCR-limited.]",
                "",
            ]
        return "\n".join(lines)


def configure_output_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def printable_ratio(text: str) -> float:
    if not text:
        return 0.0
    printable = sum(1 for char in text if char.isprintable() or char in "\r\n\t")
    return printable / len(text)


def clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\x00", "")).strip()


def is_useful_line(text: str) -> bool:
    line = clean_line(text)
    if not line or PLACEHOLDER_RE.match(line):
        return False
    return any(char.isalnum() or "\u4e00" <= char <= "\u9fff" for char in line)


def cfb_sector_offset(sector_id: int, sector_size: int) -> int:
    return (sector_id + 1) * sector_size


def read_cfb_stream(path: Path, stream_name: str) -> bytes:
    path = ensure_safe_input_file(path)
    data = read_bytes_no_follow(path, max_bytes=MAX_LEGACY_PPT_INPUT_BYTES)
    if data[:8] != b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1":
        raise ValueError(f"not a Compound File Binary document: {path}")
    if len(data) < CFB_HEADER_BYTES:
        raise ValueError(f"truncated Compound File Binary header: {path}")

    sector_shift = struct.unpack_from("<H", data, 30)[0]
    mini_sector_shift = struct.unpack_from("<H", data, 32)[0]
    if sector_shift not in VALID_SECTOR_SHIFTS or mini_sector_shift != VALID_MINI_SECTOR_SHIFT:
        raise ValueError(f"unsupported Compound File Binary sector geometry: {path}")
    sector_size = 1 << sector_shift
    mini_sector_size = 1 << mini_sector_shift
    if len(data) < sector_size:
        raise ValueError(f"truncated Compound File Binary sector area: {path}")
    total_sectors = len(data) // sector_size - 1
    fat_sector_count = struct.unpack_from("<I", data, 44)[0]
    directory_start = struct.unpack_from("<i", data, 48)[0]
    mini_stream_cutoff = struct.unpack_from("<I", data, 56)[0]
    mini_fat_start = struct.unpack_from("<i", data, 60)[0]
    mini_fat_sector_count = struct.unpack_from("<I", data, 64)[0]
    difat_start = struct.unpack_from("<i", data, 68)[0]
    difat_sector_count = struct.unpack_from("<I", data, 72)[0]
    if any(count > total_sectors for count in (fat_sector_count, mini_fat_sector_count, difat_sector_count)):
        raise ValueError(f"Compound File Binary sector count exceeds file bounds: {path}")

    def sector_payload(sector_id: int) -> bytes:
        if sector_id < 0 or sector_id >= total_sectors:
            raise ValueError(f"Compound File Binary sector is outside file bounds: {sector_id}")
        offset = cfb_sector_offset(sector_id, sector_size)
        end = offset + sector_size
        if end > len(data):
            raise ValueError(f"truncated Compound File Binary sector: {sector_id}")
        return data[offset:end]

    difat = list(struct.unpack_from("<109i", data, 76))
    current = difat_start
    seen: set[int] = set()
    if difat_sector_count == 0 and current not in (END_OF_CHAIN, FREE_SECTOR, NO_STREAM):
        raise ValueError(f"Compound File Binary has a DIFAT start without DIFAT sectors: {path}")
    for _ in range(difat_sector_count):
        if current in (END_OF_CHAIN, FREE_SECTOR, NO_STREAM) or current in seen:
            raise ValueError(f"Compound File Binary DIFAT chain is shorter than declared: {path}")
        seen.add(current)
        entries = list(struct.unpack(f"<{sector_size // 4}i", sector_payload(current)))
        difat.extend(entries[:-1])
        current = entries[-1]

    fat_sectors = [sector for sector in difat if sector >= 0][:fat_sector_count]
    fat: list[int] = []
    for sector in fat_sectors:
        fat.extend(struct.unpack(f"<{sector_size // 4}i", sector_payload(sector)))

    def read_chain(start_sector: int) -> bytes:
        output = bytearray()
        sector = start_sector
        chain_seen: set[int] = set()
        while sector >= 0 and sector not in chain_seen:
            chain_seen.add(sector)
            output.extend(sector_payload(sector))
            if sector >= len(fat):
                break
            sector = fat[sector]
            if sector == END_OF_CHAIN:
                break
        return bytes(output)

    directory_data = read_chain(directory_start)
    root_entry: dict[str, int | str] | None = None
    target_entry: dict[str, int | str] | None = None

    for offset in range(0, len(directory_data), 128):
        entry = directory_data[offset : offset + 128]
        if len(entry) < 128:
            continue
        name_length = struct.unpack_from("<H", entry, 64)[0]
        if name_length < 2:
            continue
        name = entry[: name_length - 2].decode("utf-16le", errors="ignore")
        entry_type = entry[66]
        start_sector = struct.unpack_from("<i", entry, 116)[0]
        size = struct.unpack_from("<Q", entry, 120)[0]
        item = {
            "name": name,
            "type": entry_type,
            "start": start_sector,
            "size": size,
        }
        if entry_type == 5:
            root_entry = item
        if name == stream_name:
            target_entry = item

    if target_entry is None:
        raise KeyError(f"stream not found: {stream_name}")

    start = int(target_entry["start"])
    size = int(target_entry["size"])
    if size < mini_stream_cutoff and root_entry is not None:
        mini_fat = read_chain(mini_fat_start)[: mini_fat_sector_count * sector_size]
        mini_entries = list(struct.unpack_from(f"<{len(mini_fat) // 4}i", mini_fat, 0)) if mini_fat else []
        root_stream = read_chain(int(root_entry["start"]))[: int(root_entry["size"])]
        output = bytearray()
        mini_sector = start
        chain_seen: set[int] = set()
        while mini_sector >= 0 and mini_sector not in chain_seen:
            chain_seen.add(mini_sector)
            offset = mini_sector * mini_sector_size
            output.extend(root_stream[offset : offset + mini_sector_size])
            if mini_sector >= len(mini_entries):
                break
            mini_sector = mini_entries[mini_sector]
            if mini_sector == END_OF_CHAIN:
                break
        return bytes(output[:size])

    return read_chain(start)[:size]


def append_useful_lines(text: str, output: list[str]) -> bool:
    found = False
    for line in text.splitlines():
        if is_useful_line(line):
            output.append(clean_line(line))
            found = True
    return found


def parse_ppt_records(
    payload: bytes | memoryview,
    output: list[str],
    *,
    _depth: int = 0,
    _record_budget: list[int] | None = None,
) -> int:
    if _depth > MAX_PPT_RECORD_NESTING:
        raise ValueError("legacy PPT record nesting exceeds safety limit")
    if _record_budget is None:
        _record_budget = [MAX_PPT_RECORDS]
    payload = memoryview(payload)
    position = 0
    payload_length = len(payload)
    text_record_count = 0
    while position + 8 <= payload_length:
        instance_version, record_type, record_length = struct.unpack_from("<HHI", payload, position)
        _record_budget[0] -= 1
        if _record_budget[0] < 0:
            raise ValueError("legacy PPT record count exceeds safety limit")
        version = instance_version & 0xF
        start = position + 8
        end = start + record_length
        if end > payload_length:
            break
        body = payload[start:end]

        if record_type in (4000, 4026):
            text_record_count += 1
            text = body.tobytes().decode("utf-16le", errors="ignore")
            if printable_ratio(text) > 0.65:
                append_useful_lines(text, output)
        elif record_type == 4008:
            text_record_count += 1
            for encoding in ("gbk", "cp1252", "utf-8"):
                text = body.tobytes().decode(encoding, errors="ignore")
                if printable_ratio(text) > 0.65:
                    append_useful_lines(text, output)
                    break

        if version == 0xF and record_length:
            text_record_count += parse_ppt_records(
                body,
                output,
                _depth=_depth + 1,
                _record_budget=_record_budget,
            )
        position = end

    return text_record_count


def join_deduped(lines: list[str]) -> str:
    output: list[str] = []
    previous = ""
    for line in lines:
        cleaned = clean_line(line)
        if not cleaned or cleaned == previous:
            continue
        output.append(cleaned)
        previous = cleaned
    return "\n".join(output).strip()


def extract_legacy_ppt_text(path: Path) -> LegacyPptTextResult:
    stream = read_cfb_stream(path, "PowerPoint Document")
    lines: list[str] = []
    text_record_count = parse_ppt_records(stream, lines)
    return LegacyPptTextResult(source=path, text=join_deduped(lines), text_record_count=text_record_count)


def main() -> int:
    configure_output_encoding()
    parser = argparse.ArgumentParser(description="Extract best-effort text records from legacy .ppt files.")
    parser.add_argument("ppt", type=Path, help="Path to a legacy .ppt file")
    parser.add_argument("--out", type=Path, help="Output Markdown path")
    args = parser.parse_args()

    try:
        args.ppt = ensure_safe_input_file(args.ppt)
        result = extract_legacy_ppt_text(args.ppt)
    except Exception as exc:
        print(f"ERROR: {args.ppt}: {exc}", file=sys.stderr)
        return 1

    if args.out:
        try:
            safe_write_text(args.out, result.markdown)
        except (OSError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
    else:
        print(result.markdown, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
