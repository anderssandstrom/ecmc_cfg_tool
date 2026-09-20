#!/usr/bin/env python3
"""Parsing and remote command helpers for the EtherCAT SDO browser."""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Sequence


SDO_RE = re.compile(r'^SDO\s+(0x[0-9a-fA-F]+),\s*"(.*)"\s*$')
ENTRY_RE = re.compile(
    r'^\s+(0x[0-9a-fA-F]+):([0-9a-fA-F]{2}),\s*([^,]+),\s*([^,]+),\s*([^,]+),\s*"(.*)"\s*$'
)
SSH_CONTROL_PATH = "/tmp/ecmc_sdo_ssh_%C"
SSH_CONTROL_PERSIST_SECONDS = 600


@dataclass
class SdoEntry:
    index: str
    subindex: str
    access: str
    data_type: str
    bit_length: str
    name: str

    @property
    def readable(self) -> bool:
        return "r" in self.access.lower()

    @property
    def writable(self) -> bool:
        return "w" in self.access.lower()


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


def build_remote_command(arguments: Sequence[str]) -> str:
    return " ".join(shlex.quote(part) for part in ("ethercat", *arguments))


def build_ssh_command(host: str, arguments: Sequence[str]) -> list[str]:
    return [
        "ssh",
        "-o", "ControlMaster=auto",
        "-o", f"ControlPersist={SSH_CONTROL_PERSIST_SECONDS}",
        "-o", f"ControlPath={SSH_CONTROL_PATH}",
        host,
        build_remote_command(arguments),
    ]


def sdos_arguments(master: str, slave: str) -> list[str]:
    return ["sdos", "-m", str(master), "-p", str(slave)]


def upload_arguments(master: str, slave: str, entry: SdoEntry) -> list[str]:
    arguments = [
        "upload", "-m", str(master), "-p", str(slave),
        entry.index, entry.subindex,
    ]
    if not entry.data_type.lower().startswith("type "):
        arguments.extend(["--type", entry.data_type])
    return arguments


def download_arguments(master: str, slave: str, entry: SdoEntry, value: str) -> list[str]:
    arguments = [
        "download", "-m", str(master), "-p", str(slave),
        entry.index, entry.subindex,
    ]
    if not entry.data_type.lower().startswith("type "):
        arguments.extend(["--type", entry.data_type])
    arguments.append(value)
    return arguments


def normalized_upload_value(output: str) -> str:
    """Return the value part of typical `ethercat upload` output."""
    value = str(output or "").strip()
    parts = value.split()
    if len(parts) >= 2 and parts[0].lower().startswith("0x"):
        return parts[-1]
    return value


def entry_byte_size(entry: SdoEntry) -> int | None:
    match = re.search(r"(\d+)\s*bit", entry.bit_length, re.IGNORECASE)
    if not match:
        return None
    bits = int(match.group(1))
    return (bits + 7) // 8


def ecmc_add_sdo_line(slave: str, entry: SdoEntry, value: str) -> str:
    size = entry_byte_size(entry)
    normalized = normalized_upload_value(value)
    label = f"{entry.index}:{entry.subindex[2:]} {entry.name}".strip()
    if size is None or size > 4:
        return f"# Unsupported by Cfg.EcAddSdo: {label}, {entry.bit_length}, value={normalized}"
    if not re.fullmatch(r"[-+]?(?:0[xX][0-9a-fA-F]+|\d+)", normalized):
        return f"# Non-integer value requires another SDO command: {label}, value={normalized}"
    return (
        f'ecmcConfigOrDie "Cfg.EcAddSdo(${{ECMC_EC_SLAVE_NUM={slave}}},'
        f'{entry.index},{entry.subindex},{normalized},{size})"'
    )
