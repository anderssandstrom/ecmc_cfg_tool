#!/usr/bin/env python3
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from qt_compat import QtCore, QtGui, QtWidgets

from ecmc_stream_qt import CompactDoubleSpinBox, EpicsClient, _join_prefix_pv


APP_LAUNCH_PLACEHOLDER = "Open app..."
APP_LAUNCH_RTLOG = "New RT Logger App"
APP_LAUNCH_STREAM = "Stream App"
APP_LAUNCH_AXIS = "Axis Cfg App"
APP_LAUNCH_CONTROLLER = "Cntrl Cfg App"
APP_LAUNCH_MOTION = "Motion App"
APP_LAUNCH_ISO230 = "ISO230 App"
APP_LAUNCH_DAQ = "DAQ App"
APP_LAUNCH_CAQTDM_MAIN = "caqtdm Main"

LEVEL_COLORS = {
    "INFO": "#1d4ed8",
    "ERROR": "#b91c1c",
}


def _parse_int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except Exception:
        return int(default)


def _truthy_pv(value):
    s = str(value or "").strip().strip('"').lower()
    if s in {"1", "true", "yes", "on", "enabled"}:
        return True
    if s in {"0", "false", "no", "off", "disabled"}:
        return False
    try:
        return float(s) != 0.0
    except Exception:
        return False


class RtLogWindow(QtWidgets.QMainWindow):
    def __init__(self, prefix, timeout, poll_ms=250, history_limit=200, launch_axis_id="1"):
        super().__init__()
        self._base_title = "ecmc RT Logger"
        self.setWindowTitle(self._base_title)
        self.resize(760, 520)

        self.client = EpicsClient(timeout=timeout)
        self.default_prefix = str(prefix or "").strip()
        self._last_count = None
        self._history_limit = max(10, int(history_limit or 200))
        self._updating_ctrl_widgets = False

        self._build_ui(timeout=float(timeout), poll_ms=int(poll_ms or 250), launch_axis_id=launch_axis_id)
        self._log(f"Connected via backend: {self.client.backend}")

        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.setInterval(max(50, int(poll_ms or 250)))
        self._poll_timer.timeout.connect(self.refresh_status)
        self._poll_timer.start()
        QtCore.QTimer.singleShot(0, self.refresh_status)

    def _build_ui(self, timeout, poll_ms, launch_axis_id):
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(4)

        self.cfg_toggle_btn = QtWidgets.QPushButton("Hide Config")
        self.cfg_toggle_btn.setAutoDefault(False)
        self.cfg_toggle_btn.setDefault(False)
        self.cfg_toggle_btn.clicked.connect(self._toggle_config_panel)

        self.app_log_toggle_btn = QtWidgets.QPushButton("Show App Log")
        self.app_log_toggle_btn.setAutoDefault(False)
        self.app_log_toggle_btn.setDefault(False)
        self.app_log_toggle_btn.clicked.connect(self._toggle_app_log_panel)

        self.open_app_combo = QtWidgets.QComboBox()
        self.open_app_combo.setMinimumWidth(170)
        self.open_app_combo.addItem(APP_LAUNCH_PLACEHOLDER, "")
        self.open_app_combo.addItem(APP_LAUNCH_RTLOG, "rtlog")
        self.open_app_combo.addItem(APP_LAUNCH_STREAM, "stream")
        self.open_app_combo.addItem(APP_LAUNCH_AXIS, "axis")
        self.open_app_combo.addItem(APP_LAUNCH_CONTROLLER, "controller")
        self.open_app_combo.addItem(APP_LAUNCH_MOTION, "motion")
        self.open_app_combo.addItem(APP_LAUNCH_ISO230, "iso230")
        self.open_app_combo.addItem(APP_LAUNCH_DAQ, "daq")
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_MAIN, "caqtdm_main")
        self.open_app_combo.activated.connect(self._on_open_app_selected)

        for w in (self.cfg_toggle_btn, self.app_log_toggle_btn, self.open_app_combo):
            try:
                w.setMaximumHeight(24)
            except Exception:
                pass

        top_row.addWidget(self.cfg_toggle_btn)
        top_row.addWidget(self.app_log_toggle_btn)
        top_row.addWidget(QtWidgets.QLabel("Launch"))
        top_row.addWidget(self.open_app_combo)
        top_row.addStretch(1)
        layout.addLayout(top_row)

        self.cfg_group = QtWidgets.QGroupBox("Logger Configuration")
        cfg = QtWidgets.QGridLayout(self.cfg_group)
        cfg.setContentsMargins(6, 6, 6, 6)
        cfg.setHorizontalSpacing(4)
        cfg.setVerticalSpacing(4)

        self.prefix_edit = QtWidgets.QLineEdit(self.default_prefix or "IOC:ECMC")
        self.launch_axis_edit = QtWidgets.QLineEdit(str(launch_axis_id or "1").strip() or "1")
        self.launch_axis_edit.setMaximumWidth(80)
        self.timeout_edit = CompactDoubleSpinBox()
        self.timeout_edit.setRange(0.1, 60.0)
        self.timeout_edit.setDecimals(1)
        self.timeout_edit.setValue(float(timeout))
        self.timeout_edit.valueChanged.connect(self._set_timeout)
        self.poll_spin = QtWidgets.QSpinBox()
        self.poll_spin.setRange(50, 10000)
        self.poll_spin.setSuffix(" ms")
        self.poll_spin.setValue(max(50, int(poll_ms)))
        self.poll_spin.valueChanged.connect(self._set_poll_ms)
        self.history_spin = QtWidgets.QSpinBox()
        self.history_spin.setRange(10, 5000)
        self.history_spin.setValue(self._history_limit)
        self.history_spin.valueChanged.connect(self._set_history_limit)

        refresh_btn = QtWidgets.QPushButton("Read Status")
        refresh_btn.setAutoDefault(False)
        refresh_btn.setDefault(False)
        refresh_btn.clicked.connect(self.refresh_status)
        clear_history_btn = QtWidgets.QPushButton("Clear History")
        clear_history_btn.setAutoDefault(False)
        clear_history_btn.setDefault(False)
        clear_history_btn.clicked.connect(self._clear_history)

        cfg.addWidget(QtWidgets.QLabel("IOC Prefix"), 0, 0)
        cfg.addWidget(self.prefix_edit, 0, 1)
        cfg.addWidget(QtWidgets.QLabel("Launch Axis"), 0, 2)
        cfg.addWidget(self.launch_axis_edit, 0, 3)
        cfg.addWidget(QtWidgets.QLabel("Timeout [s]"), 0, 4)
        cfg.addWidget(self.timeout_edit, 0, 5)

        cfg.addWidget(QtWidgets.QLabel("Poll"), 1, 0)
        cfg.addWidget(self.poll_spin, 1, 1)
        cfg.addWidget(QtWidgets.QLabel("History Limit"), 1, 2)
        cfg.addWidget(self.history_spin, 1, 3)
        cfg.addWidget(refresh_btn, 1, 4)
        cfg.addWidget(clear_history_btn, 1, 5)
        layout.addWidget(self.cfg_group)

        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(4)

        self.status_group = QtWidgets.QGroupBox("RT Logger Status")
        status = QtWidgets.QGridLayout(self.status_group)
        status.setContentsMargins(6, 6, 6, 6)
        status.setHorizontalSpacing(4)
        status.setVerticalSpacing(4)

        self.backend_edit = QtWidgets.QLineEdit(str(self.client.backend or ""))
        self.backend_edit.setReadOnly(True)
        self.level_edit = QtWidgets.QLineEdit()
        self.level_edit.setReadOnly(True)
        self.level_text_edit = QtWidgets.QLineEdit()
        self.level_text_edit.setReadOnly(True)
        self.count_edit = QtWidgets.QLineEdit()
        self.count_edit.setReadOnly(True)
        self.drop_count_edit = QtWidgets.QLineEdit()
        self.drop_count_edit.setReadOnly(True)
        self.ctrl_rb_edit = QtWidgets.QLineEdit()
        self.ctrl_rb_edit.setReadOnly(True)
        self.last_msg_edit = QtWidgets.QPlainTextEdit()
        self.last_msg_edit.setReadOnly(True)
        self.last_msg_edit.setMaximumHeight(64)

        status.addWidget(QtWidgets.QLabel("Backend"), 0, 0)
        status.addWidget(self.backend_edit, 0, 1)
        status.addWidget(QtWidgets.QLabel("Level"), 0, 2)
        status.addWidget(self.level_edit, 0, 3)
        status.addWidget(QtWidgets.QLabel("Level Text"), 1, 0)
        status.addWidget(self.level_text_edit, 1, 1)
        status.addWidget(QtWidgets.QLabel("Message Count"), 1, 2)
        status.addWidget(self.count_edit, 1, 3)
        status.addWidget(QtWidgets.QLabel("Dropped Count"), 2, 0)
        status.addWidget(self.drop_count_edit, 2, 1)
        status.addWidget(QtWidgets.QLabel("Control RB"), 2, 2)
        status.addWidget(self.ctrl_rb_edit, 2, 3)
        status.addWidget(QtWidgets.QLabel("Last Message"), 3, 0)
        status.addWidget(self.last_msg_edit, 3, 1, 1, 3)
        status_row.addWidget(self.status_group, 2)

        self.control_group = QtWidgets.QGroupBox("Logger Control")
        ctrl = QtWidgets.QGridLayout(self.control_group)
        ctrl.setContentsMargins(6, 6, 6, 6)
        ctrl.setHorizontalSpacing(4)
        ctrl.setVerticalSpacing(4)

        self.ctrl_write_spin = QtWidgets.QSpinBox()
        self.ctrl_write_spin.setRange(0, 0x7FFFFFFF)
        self.ctrl_write_spin.setValue(3)
        self.info_enable_chk = QtWidgets.QCheckBox("INFO Enabled")
        self.err_enable_chk = QtWidgets.QCheckBox("ERROR Enabled")
        self.apply_ctrl_btn = QtWidgets.QPushButton("Apply Control")
        self.apply_ctrl_btn.setAutoDefault(False)
        self.apply_ctrl_btn.setDefault(False)
        self.apply_ctrl_btn.clicked.connect(self._apply_control)
        self.ctrl_write_spin.valueChanged.connect(self._sync_checks_from_word)
        self.info_enable_chk.toggled.connect(self._sync_word_from_checks)
        self.err_enable_chk.toggled.connect(self._sync_word_from_checks)

        ctrl.addWidget(QtWidgets.QLabel("Control Word"), 0, 0)
        ctrl.addWidget(self.ctrl_write_spin, 0, 1)
        ctrl.addWidget(self.apply_ctrl_btn, 0, 2)
        ctrl.addWidget(self.info_enable_chk, 1, 0, 1, 2)
        ctrl.addWidget(self.err_enable_chk, 2, 0, 1, 2)
        status_row.addWidget(self.control_group, 1)
        layout.addLayout(status_row)

        history_group = QtWidgets.QGroupBox("Buffered Logger Messages")
        history_layout = QtWidgets.QVBoxLayout(history_group)
        history_layout.setContentsMargins(6, 6, 6, 6)
        history_layout.setSpacing(4)
        self.history_list = QtWidgets.QListWidget()
        self.history_list.setAlternatingRowColors(True)
        history_layout.addWidget(self.history_list, stretch=1)
        layout.addWidget(history_group, stretch=1)

        self.app_log = QtWidgets.QPlainTextEdit()
        self.app_log.setReadOnly(True)
        self.app_log.setMaximumHeight(110)
        self.app_log.setVisible(False)
        layout.addWidget(self.app_log)

    def _toggle_config_panel(self):
        visible = not self.cfg_group.isVisible()
        self.cfg_group.setVisible(visible)
        self.cfg_toggle_btn.setText("Hide Config" if visible else "Show Config")

    def _toggle_app_log_panel(self):
        visible = not self.app_log.isVisible()
        self.app_log.setVisible(visible)
        self.app_log_toggle_btn.setText("Hide App Log" if visible else "Show App Log")

    def _set_timeout(self, value):
        self.client.timeout = float(value)

    def _set_poll_ms(self, value):
        if hasattr(self, "_poll_timer"):
            self._poll_timer.setInterval(max(50, int(value or 250)))

    def _set_history_limit(self, value):
        self._history_limit = max(10, int(value or 200))
        while self.history_list.count() > self._history_limit:
            self.history_list.takeItem(self.history_list.count() - 1)

    def _clear_history(self):
        self.history_list.clear()
        self._log("Cleared buffered logger message list")

    def _log(self, msg):
        t = datetime.now().strftime("%H:%M:%S")
        self.app_log.appendPlainText(f"[{t}] {msg}")

    def _pv(self, suffix):
        return _join_prefix_pv(self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC", suffix)

    def _get_pv_text(self, suffix):
        return str(self.client.get(self._pv(suffix), as_string=True) or "").strip()

    def _set_level_color(self, level_text):
        level = str(level_text or "").strip().upper()
        color = LEVEL_COLORS.get(level, "#1f2937")
        self.level_edit.setStyleSheet(f"color: {color}; font-weight: 600;")
        self.level_text_edit.setStyleSheet(f"color: {color}; font-weight: 600;")

    def _sync_checks_from_word(self, value):
        if self._updating_ctrl_widgets:
            return
        self._updating_ctrl_widgets = True
        word = int(value or 0)
        self.info_enable_chk.setChecked(bool(word & 0x1))
        self.err_enable_chk.setChecked(bool(word & 0x2))
        self._updating_ctrl_widgets = False

    def _sync_word_from_checks(self, _checked=False):
        if self._updating_ctrl_widgets:
            return
        self._updating_ctrl_widgets = True
        word = 0
        if self.info_enable_chk.isChecked():
            word |= 0x1
        if self.err_enable_chk.isChecked():
            word |= 0x2
        self.ctrl_write_spin.setValue(word)
        self._updating_ctrl_widgets = False

    def _apply_control(self):
        word = int(self.ctrl_write_spin.value())
        try:
            self.client.put(self._pv("MCU-RTLog-Ctrl"), word, wait=True)
            self._log(f"Applied logger control word {word}")
            self.refresh_status()
        except Exception as ex:
            self._log(f"Failed to write logger control word: {ex}")

    def _append_history_item(self, text, level_text="INFO", synthetic=False):
        stamp = datetime.now().strftime("%H:%M:%S")
        prefix = f"[{stamp}]"
        level = str(level_text or "INFO").strip().upper() or "INFO"
        line = f"{prefix} {level}: {text}" if not synthetic else f"{prefix} {text}"
        item = QtWidgets.QListWidgetItem(line)
        color = "#6b7280" if synthetic else LEVEL_COLORS.get(level, "#1f2937")
        item.setForeground(QtGui.QBrush(QtGui.QColor(color)))
        if level == "ERROR" and not synthetic:
            font = item.font()
            font.setBold(True)
            item.setFont(font)
        self.history_list.insertItem(0, item)
        while self.history_list.count() > self._history_limit:
            self.history_list.takeItem(self.history_list.count() - 1)

    def _refresh_control_state(self, ctrl_rb_text, info_text, err_text):
        self._updating_ctrl_widgets = True
        ctrl_rb = _parse_int(ctrl_rb_text, default=self.ctrl_write_spin.value())
        self.ctrl_rb_edit.setText(str(ctrl_rb_text))
        self.ctrl_write_spin.setValue(ctrl_rb)
        self.info_enable_chk.setChecked(_truthy_pv(info_text))
        self.err_enable_chk.setChecked(_truthy_pv(err_text))
        self._updating_ctrl_widgets = False

    def refresh_status(self):
        try:
            level = self._get_pv_text("MCU-RTLog-Level")
            level_text = self._get_pv_text("MCU-RTLog-LevelTxt")
            msg = self._get_pv_text("MCU-RTLog-Msg")
            count = _parse_int(self._get_pv_text("MCU-RTLog-Cnt"), default=0)
            drop_count = self._get_pv_text("MCU-RTLog-DropCnt")
            ctrl_rb = self._get_pv_text("MCU-RTLog-Ctrl-RB")
            info_ena = self._get_pv_text("MCU-RTLog-InfoEna")
            err_ena = self._get_pv_text("MCU-RTLog-ErrEna")
        except Exception as ex:
            self._log(f"Failed to read RT logger PVs: {ex}")
            return

        self.backend_edit.setText(str(self.client.backend or ""))
        self.level_edit.setText(level)
        self.level_text_edit.setText(level_text)
        self._set_level_color(level_text or level)
        self.count_edit.setText(str(count))
        self.drop_count_edit.setText(drop_count)
        self.last_msg_edit.setPlainText(msg)
        self._refresh_control_state(ctrl_rb, info_ena, err_ena)
        self.setWindowTitle(f"{self._base_title} [{self.prefix_edit.text().strip() or self.default_prefix or 'IOC:ECMC'}]")

        if self._last_count is None:
            self._last_count = count
            if count > 0 and msg:
                self._append_history_item(msg, level_text or level)
            return
        if count == self._last_count:
            return
        delta = max(1, count - int(self._last_count))
        if delta > 1:
            self._append_history_item(
                f"{delta - 1} message(s) elapsed between polls; latest message shown below",
                synthetic=True,
            )
        if msg:
            self._append_history_item(msg, level_text or level)
        self._last_count = count

    def _reset_open_app_combo(self):
        self.open_app_combo.blockSignals(True)
        self.open_app_combo.setCurrentIndex(0)
        self.open_app_combo.blockSignals(False)

    def _on_open_app_selected(self, index):
        action = str(self.open_app_combo.itemData(index) or "")
        try:
            if action == "rtlog":
                self._open_rtlog_window()
            elif action == "stream":
                self._open_stream_window()
            elif action == "axis":
                self._open_axis_window()
            elif action == "controller":
                self._open_controller_window()
            elif action == "motion":
                self._open_motion_window()
            elif action == "iso230":
                self._open_iso230_window()
            elif action == "daq":
                self._open_daq_window()
            elif action == "caqtdm_main":
                self._open_caqtdm_main_panel()
        finally:
            self._reset_open_app_combo()

    def _current_prefix(self):
        return self.prefix_edit.text().strip() or self.default_prefix or "IOC:ECMC"

    def _current_axis_id(self):
        return self.launch_axis_edit.text().strip() or "1"

    def _open_script(self, script_name, label, args=None):
        script = Path(__file__).with_name(script_name)
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return False
        cmd = ["bash", str(script)] + [str(a) for a in (args or [])]
        try:
            subprocess.Popen(
                cmd,
                cwd=str(script.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started {label} window")
            return True
        except Exception as ex:
            self._log(f"Failed to start {label} window: {ex}")
            return False

    def _open_rtlog_window(self):
        self._open_script("start_rtlog.sh", "RT logger", [self._current_prefix()])

    def _open_stream_window(self):
        self._open_script("start.sh", "stream", [self._current_prefix()])

    def _open_daq_window(self):
        self._open_script("start_daq.sh", "DAQ", [self._current_prefix()])

    def _open_axis_window(self):
        self._open_script("start_axis.sh", "axis", [self._current_prefix(), self._current_axis_id()])

    def _open_controller_window(self):
        self._open_script("start_cntrl.sh", "controller", [self._current_prefix(), self._current_axis_id()])

    def _open_motion_window(self):
        self._open_script("start_mtn.sh", "motion", [self._current_prefix(), self._current_axis_id()])

    def _open_iso230_window(self):
        self._open_script("start_iso230.sh", "ISO230", [self._current_prefix(), self._current_axis_id()])

    def _open_caqtdm_main_panel(self):
        macro = f"IOC={self._current_prefix()}"
        try:
            cmd = f'caqtdm -macro "{macro}" ecmcMain.ui'
            subprocess.Popen(
                ["bash", "-lc", cmd],
                cwd=str(Path(__file__).resolve().parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._log(f"Started caQtDM main panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM main panel: {ex}")


def main():
    ap = argparse.ArgumentParser(description="Qt app for ecmc RT logger status and message history")
    ap.add_argument("--prefix", default="", help="IOC prefix (e.g. IOC:ECMC)")
    ap.add_argument("--timeout", type=float, default=2.0, help="EPICS timeout [s]")
    ap.add_argument("--poll-ms", type=int, default=250, help="Logger poll interval [ms]")
    ap.add_argument("--history-limit", type=int, default=200, help="Max number of buffered messages")
    ap.add_argument("--axis-id", default="1", help="Launch-axis helper for opening axis-based apps")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    w = RtLogWindow(
        prefix=args.prefix,
        timeout=args.timeout,
        poll_ms=args.poll_ms,
        history_limit=args.history_limit,
        launch_axis_id=args.axis_id,
    )
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
