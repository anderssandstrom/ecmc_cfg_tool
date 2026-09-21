#!/usr/bin/env python3
"""Forward an OpenSSH askpass request to the SDO browser's Qt window."""

from __future__ import annotations

import os
import socket
import sys


def main():
    socket_path = os.environ.get("ECMC_SDO_ASKPASS_SOCKET")
    if not socket_path:
        return 1
    prompt = sys.argv[1] if len(sys.argv) > 1 else "SSH password:"
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(120)
            connection.connect(socket_path)
            connection.sendall(prompt.encode("utf-8"))
            connection.shutdown(socket.SHUT_WR)
            chunks = []
            while chunk := connection.recv(4096):
                chunks.append(chunk)
    except OSError:
        return 1
    response = b"".join(chunks)
    if not response.startswith(b"\x01"):
        return 1
    sys.stdout.write(response[1:].decode("utf-8") + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
