#!/usr/bin/env python3
"""Qt dialog invoked by OpenSSH through SSH_ASKPASS."""

from __future__ import annotations

import sys

from qt_compat import QtWidgets


def main():
    app = QtWidgets.QApplication(sys.argv)
    prompt = sys.argv[1] if len(sys.argv) > 1 else "SSH password:"
    host_key_question = "yes/no" in prompt.lower() or "continue connecting" in prompt.lower()
    echo = QtWidgets.QLineEdit.Normal if host_key_question else QtWidgets.QLineEdit.Password
    answer, accepted = QtWidgets.QInputDialog.getText(None, "SSH authentication", prompt, echo)
    if not accepted:
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
