"""Python front end for the IOC's master-level EtherCAT tool records."""
from threading import Lock
from time import monotonic

from ecmc_epics import EpicsClient, _join_prefix_pv
from qt_compat import QtCore, QtWidgets


# The menu writes mbbo VAL indices. The IOC maps them to command and state codes.
COMMANDS = (
    ("Master information", 1),
    ("List slaves", 2),
    ("Change slave state", 3),
    ("Slave hardware diagnostics", 4),
    ("EL7062 autotune", 5),
    ("EL7062 tuning readback", 6),
)
STATES = ("INIT", "PREOP", "BOOT", "SAFEOP", "OP")
READY = {"IDLE", "ERROR"}
_submit_lock = Lock()
Signal = getattr(QtCore, "pyqtSignal", None) or QtCore.Signal


def _read_status(client, base):
    status = str(client.get(base + "-Stat")).strip().upper()
    return {"0": "IDLE", "1": "BUSY", "2": "ERROR",
            "0.0": "IDLE", "1.0": "BUSY", "2.0": "ERROR"}.get(status, status)


def _submit(client, base, command, slave, state, channel):
    with _submit_lock:
        status = _read_status(client, base)
        if status not in READY:
            raise RuntimeError(f"Cannot run a command while the IOC reports {status or 'unknown status'}.")
        if command >= 3:
            client.put(base + ".D", slave)
        if command == 3:
            client.put(base + "-StateArgs", state)
        if command in (5, 6):
            client.put(base + ".E", channel)
        client.put(base + "-Cmd", command)
        baseline = client.get_char_waveform(base + "-Msg", with_timestamp=True) if command != 3 else None
        was_disabled = float(client.get(base + ".U", as_string=False)) == 0
        if was_disabled:
            client.put(base + ".U", 1)
        try:
            client.put(base + ".PROC", 1)
        finally:
            if was_disabled:
                client.put(base + ".U", 0)
        return baseline


class _TaskSignals(QtCore.QObject):
    finished = Signal(object, str, bool)


class _EcToolTask(QtCore.QRunnable):
    def __init__(self, base, timeout, request, read_output):
        super().__init__()
        self.signals = _TaskSignals()
        self.base = base
        self.timeout = timeout
        self.request = request
        self.read_output = read_output

    def run(self):
        try:
            client = EpicsClient(timeout=self.timeout)
            if self.request is not None:
                baseline = _submit(client, self.base, *self.request)
                self.signals.finished.emit(baseline, "", True)
                return
            status = _read_status(client, self.base)
            progress = int(float(client.get(self.base + "-Prgs", as_string=False)))
            output = (
                client.get_char_waveform(self.base + "-Msg", with_timestamp=True)
                if self.read_output and status in READY else None
            )
            self.signals.finished.emit((status, progress, output), "", False)
        except Exception as ex:
            self.signals.finished.emit(None, str(ex), self.request is not None)


class EthercatToolWindow(QtWidgets.QWidget):
    def __init__(self, prefix, master, slave, timeout=1.0, demo=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"EtherCAT Tool · {prefix} · Master {master} · Slave {slave}")
        self.resize(760, 510)
        self._base = _join_prefix_pv(prefix, f"m{int(master)}-EcTool")
        self._timeout = timeout
        self._demo = demo
        self._task = None
        self._pending = None
        self._auto_run = not demo
        self._startup_slave = int(slave)
        self._baseline = None
        self._saw_busy = False
        self._submitted_at = 0.0
        self._closed = False
        self._can_run = demo

        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(QtWidgets.QLabel(f"IOC {prefix}   |   Master {master}"))
        form = QtWidgets.QFormLayout()
        layout.addLayout(form)
        self.slave = QtWidgets.QSpinBox()
        self.slave.setRange(0, 65535)
        self.slave.setValue(int(slave))
        form.addRow("Slave position", self.slave)
        self.command = QtWidgets.QComboBox()
        for label, index in COMMANDS:
            self.command.addItem(label, index)
        self.command.setCurrentIndex(3)
        form.addRow("Command", self.command)
        self.state = QtWidgets.QComboBox()
        self.state.addItems(STATES)
        self.state.setCurrentIndex(-1)
        form.addRow("Target state", self.state)
        self.channel = QtWidgets.QSpinBox()
        self.channel.setRange(1, 2)
        form.addRow("EL7062 channel", self.channel)
        self.run_button = QtWidgets.QPushButton("Run")
        self.run_button.clicked.connect(self._run)
        form.addRow(self.run_button)

        status_row = QtWidgets.QHBoxLayout()
        self.status = QtWidgets.QLabel("Demo" if demo else "Connecting…")
        self.progress = QtWidgets.QProgressBar()
        status_row.addWidget(self.status)
        status_row.addWidget(self.progress, 1)
        layout.addLayout(status_row)
        self.output = QtWidgets.QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText("Waiting to run diagnostics…")
        layout.addWidget(self.output, 1)
        self.feedback = QtWidgets.QLabel()
        self.feedback.setWordWrap(True)
        layout.addWidget(self.feedback)

        self.command.currentIndexChanged.connect(self._update_controls)
        self.state.currentIndexChanged.connect(self._update_controls)
        self._update_controls()
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._poll)
        if demo:
            self._run()
        else:
            self.feedback.setText("Waiting to run slave hardware diagnostics…")
            self._timer.start(1000)
            self._poll()

    def _update_controls(self):
        command = self.command.currentData()
        self.slave.setEnabled(command >= 3)
        self.state.setEnabled(command == 3)
        self.channel.setEnabled(command in (5, 6))
        self.run_button.setEnabled(
            self._can_run and (command != 3 or self.state.currentIndex() >= 0)
        )

    def _start_task(self, request=None):
        self._task = _EcToolTask(self._base, self._timeout, request, self._baseline is not None)
        self._task.signals.finished.connect(self._finished)
        QtCore.QThreadPool.globalInstance().start(self._task)

    def _poll(self):
        if self._task is None and not self._closed:
            self._start_task()

    def _run(self):
        command = self.command.currentData()
        slave = self.slave.value()
        state = self.state.currentIndex() + 1
        channel = self.channel.value()
        label = self.command.currentText()
        if self._demo:
            self.progress.setValue(100)
            self.output.setPlainText(f"Demo request: {label}\nSlave: {slave}")
            return
        if command in (3, 5):
            target = (f"Request {self.state.currentText()} for slave {slave}?"
                      if command == 3 else f"Start EL7062 autotune on slave {slave}, channel {channel}?")
            answer = QtWidgets.QMessageBox.question(
                self, label, f"{self._base}\n\n{target}",
                QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No, QtWidgets.QMessageBox.No,
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self._queue_command(command, slave, state, channel)

    def _queue_command(self, command, slave, state, channel):
        # A refresh may still be running. Submit after it completes.
        self._timer.stop()
        self._can_run = False
        self._baseline = None
        self._saw_busy = False
        self.output.clear()
        self.output.setPlaceholderText("Updating…")
        self.progress.setValue(0)
        self._update_controls()
        self._pending = (command, slave, state, channel)
        self.feedback.setText("Submitting command…")
        self._submit_pending()

    def _submit_pending(self):
        if self._task is None and not self._closed and self._pending is not None:
            request, self._pending = self._pending, None
            self._start_task(request)

    def _finished(self, result, error, submitted):
        self._task = None
        if self._closed:
            return
        if submitted:
            self._baseline = result
            self._submitted_at = monotonic()
            if error:
                self.feedback.setText(f"Command submission failed: {error}")
                self.output.setPlaceholderText("Command submission failed.")
            elif self._baseline is None:
                self.feedback.setText("Command submitted to IOC.")
                self.output.setPlaceholderText("This command has no waveform output.")
            else:
                self.feedback.setText("Waiting for new IOC output…")
            self._timer.start(500)
            self._poll()
            return
        if error:
            self.status.setText("Unavailable")
            self.status.setToolTip(error)
            self.output.setPlaceholderText("IOC output unavailable.")
            self._can_run = False
        else:
            status, progress, output = result
            self.status.setText(status)
            self.status.setToolTip("")
            if self._auto_run and status in READY:
                self._auto_run = False
                self._queue_command(4, self._startup_slave, 0, 1)
                return
            if self._baseline is not None:
                self._check_output(status, progress, output)
            self._can_run = status in READY and self._baseline is None
        if self._pending is not None:
            self._can_run = False
            self._submit_pending()
        self._update_controls()

    def _check_output(self, status, progress, output):
        if status == "BUSY":
            self._saw_busy = True
            self.progress.setValue(max(0, min(100, progress)))
            return
        if output is None:
            return
        fresh = output != self._baseline
        completed_without_timestamp = output[1] is None and self._saw_busy
        if status == "IDLE" and progress >= 100 and (fresh or completed_without_timestamp):
            self.output.setPlainText(output[0])
            self.output.setPlaceholderText("The IOC returned an empty result.")
            self.progress.setValue(100)
            self.feedback.clear()
        elif status == "ERROR":
            if fresh:
                self.output.setPlainText(output[0])
            self.output.setPlaceholderText("The IOC reported a command error.")
            self.feedback.setText("IOC reported a command error.")
        elif monotonic() - self._submitted_at < 10:
            return
        else:
            self.feedback.setText("No new IOC output detected. Use Run to retry.")
            self.output.setPlaceholderText("No new IOC output detected.")
        self._baseline = None
        self._timer.start(1000)

    def closeEvent(self, event):
        self._closed = True
        self._timer.stop()
        super().closeEvent(event)
