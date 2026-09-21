#!/usr/bin/env python3
"""Parsing and remote command helpers for the EtherCAT SDO browser."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence


SDO_RE = re.compile(r'^SDO\s+(0x[0-9a-fA-F]+),\s*"(.*)"\s*$')
ENTRY_RE = re.compile(
    r'^\s+(0x[0-9a-fA-F]+):([0-9a-fA-F]{2}),\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*"(.*)"\s*$'
)
SSH_CONTROL_PATH = "/tmp/ecmc_sdo_ssh_%C"
SSH_CONTROL_PERSIST_SECONDS = 600
DEFAULT_ETHERCAT_BINARY = "/opt/etherlab/bin/ethercat"


@dataclass
class SdoEntry:
    index: str
    subindex: str
    access: str
    data_type: str
    bit_length: str
    name: str

    @property
    def bit_count(self) -> int | None:
        match = re.search(r"(\d+)\s*bit", self.bit_length, re.IGNORECASE)
        if not match:
            return None
        return int(match.group(1))

    @property
    def has_data(self) -> bool:
        return self.bit_count != 0

    @property
    def readable(self) -> bool:
        return self.has_data and "r" in self.access.lower()

    @property
    def writable(self) -> bool:
        return self.has_data and "w" in self.access.lower()

    @property
    def effective_data_type(self) -> str | None:
        if not self.data_type.lower().startswith("type "):
            return self.data_type
        byte_size = entry_byte_size(self)
        if byte_size is None or byte_size == 0:
            return None
        return f"uint{byte_size * 8}"

    @property
    def is_text_like(self) -> bool:
        data_type = self.data_type.lower().replace("_", " ")
        return "string" in data_type or "octet" in data_type

    @property
    def is_diagnostic_message(self) -> bool:
        try:
            return int(self.index, 16) == 0x10F3 and int(self.subindex, 16) >= 0x06 and self.is_text_like
        except ValueError:
            return False


@dataclass
class SdoObject:
    index: str
    name: str
    entries: list[SdoEntry] = field(default_factory=list)


def parse_sdos(text: str) -> list[SdoObject]:
    objects: list[SdoObject] = []
    current: SdoObject | None = None
    for line in text.splitlines():
        object_match = SDO_RE.match(line)
        if object_match:
            current = SdoObject(index=object_match.group(1), name=object_match.group(2))
            objects.append(current)
            continue

        entry_match = ENTRY_RE.match(line)
        if not entry_match:
            continue
        index, subindex, access, data_type, bit_length, name = entry_match.groups()
        if current is None or current.index.lower() != index.lower():
            current = SdoObject(index=index, name="")
            objects.append(current)
        current.entries.append(
            SdoEntry(
                index=index,
                subindex=f"0x{subindex}",
                access=access.strip(),
                data_type=data_type.strip(),
                bit_length=bit_length.strip(),
                name=name,
            )
        )
    return objects


def build_remote_command(arguments: Sequence[str], binary: str = DEFAULT_ETHERCAT_BINARY) -> str:
    return " ".join(shlex.quote(part) for part in (binary, *arguments))


def build_ssh_command(host: str, arguments: Sequence[str], binary: str = DEFAULT_ETHERCAT_BINARY) -> list[str]:
    return [
        "ssh",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPersist={SSH_CONTROL_PERSIST_SECONDS}",
        "-o", f"ControlPath={SSH_CONTROL_PATH}",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=10",
        "-o", "ServerAliveCountMax=2",
        host,
        build_remote_command(arguments, binary),
    ]


def sdos_arguments(master: str, slave: str) -> list[str]:
    return ["sdos", "-m", str(master), "-p", str(slave)]


def upload_arguments(master: str, slave: str, entry: SdoEntry, include_type: bool = True) -> list[str]:
    arguments = [
        "upload", "-m", str(master), "-p", str(slave),
        entry.index, entry.subindex,
    ]
    data_type = entry.effective_data_type if include_type else None
    if data_type:
        arguments.extend(["--type", data_type])
    return arguments


def download_arguments(master: str, slave: str, entry: SdoEntry, value: str) -> list[str]:
    arguments = [
        "download", "-m", str(master), "-p", str(slave),
        entry.index, entry.subindex,
    ]
    data_type = entry.effective_data_type
    if data_type:
        arguments.extend(["--type", data_type])
    arguments.append(value)
    return arguments


def normalized_upload_value(output: str) -> str:
    """Return the value part of typical `ethercat upload` output."""
    value = str(output or "").strip()
    parts = value.split()
    if len(parts) >= 2 and parts[0].lower().startswith("0x"):
        return parts[-1]
    return value


def decode_command_output(value, preserve_bytes: bool = False) -> str:
    if isinstance(value, bytes):
        if preserve_bytes:
            return value.decode("latin-1")
        return value.decode("utf-8", errors="replace")
    return value or ""


def decode_ethercat_time(ns: int) -> str:
    dt = datetime.fromtimestamp(ns / 1000000000) + timedelta(days=10957)
    return dt.isoformat(sep=" ", timespec="seconds")


def decode_diagnostic_message(raw_value: str) -> str:
    text = str(raw_value or "").strip()
    compact = re.sub(r"\s+", "", text)
    if compact.lower().startswith("0x"):
        compact = compact[2:]
    if compact and len(compact) % 2 == 0 and re.fullmatch(r"[0-9a-fA-F]+", compact):
        raw = bytes.fromhex(compact)
    else:
        raw = text.encode("latin-1", errors="ignore").rstrip(b"\r\n")
    if not raw or not any(raw):
        return ""
    if len(raw) < 16:
        return "0x" + raw.hex()
    diag_code = int.from_bytes(raw[0:4], byteorder="little")
    flags = int.from_bytes(raw[4:6], byteorder="little")
    text_id = int.from_bytes(raw[6:8], byteorder="little")
    timestamp = decode_ethercat_time(int.from_bytes(raw[8:16], byteorder="little"))
    dynamic = raw[16:]
    parts = [
        f"diag_code=0x{diag_code:08x}",
        f"flags=0x{flags:04x}",
        f"text_id=0x{text_id:04x}",
        f"time={timestamp}",
    ]
    if dynamic:
        parts.append(f"dynamic=0x{dynamic.hex()}")
    return ", ".join(parts)


def display_upload_value(entry: SdoEntry, output: str) -> str:
    """Return the editable value for an upload result."""
    value = str(output or "").strip()
    if entry.is_diagnostic_message:
        return decode_diagnostic_message(value)
    if entry.is_text_like:
        return value
    return normalized_upload_value(value)


def entry_byte_size(entry: SdoEntry) -> int | None:
    bits = entry.bit_count
    if bits is None:
        return None
    return (bits + 7) // 8


def ecmc_add_sdo_line(slave: str, entry: SdoEntry, value: str) -> str:
    size = entry_byte_size(entry)
    normalized = display_upload_value(entry, value)
    label = f"{entry.index}:{entry.subindex[2:]} {entry.name}".strip()
    comment = (
        f"# {label} | type={entry.data_type} | bits={entry.bit_length} | "
        f"access={entry.access} | value={normalized}"
    )
    if size is None or size > 4:
        return f"{comment}\n# Unsupported by Cfg.EcAddSdo: {label}, {entry.bit_length}, value={normalized}"
    if not re.fullmatch(r"[-+]?(?:0[xX][0-9a-fA-F]+|\d+)", normalized):
        return f"{comment}\n# Non-integer value requires another SDO command: {label}, value={normalized}"
    return (
        f"{comment}\n"
        f'ecmcConfigOrDie "Cfg.EcAddSdo(${{ECMC_EC_SLAVE_NUM={slave}}},'
        f'{entry.index},{entry.subindex},{normalized},{size})"'
    )
