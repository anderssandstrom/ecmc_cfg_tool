#!/usr/bin/env python3
import sys


QT_BINDING = ""

try:
    from PyQt5 import QtCore, QtGui, QtWidgets

    QT_BINDING = "PyQt5"
except Exception as pyqt_error:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore

        QT_BINDING = "PySide6"
    except Exception as pyside_error:
        raise ModuleNotFoundError(
            "No Qt Python binding is available for this interpreter. "
            f"Checked {sys.executable}. "
            "Install PyQt5 or PySide6, or launch the app with a Python environment "
            "that already provides one."
        ) from pyside_error
