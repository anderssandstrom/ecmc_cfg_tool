import weakref
"""Native source and variable browser for compile-time PLC metadata."""
import re
from concurrent.futures import ThreadPoolExecutor
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_plc_metadata import fetch_metadata


class PlcHighlighter(QtGui.QSyntaxHighlighter):
    def highlightBlock(self, text):
        for pattern, color in ((r'\b(if|else|while|for|var|return|and|or|not)\b', '#7258bb'),
                               (r'\b\d+(?:\.\d+)?\b', '#287bc0'),
                               (r'//.*|#.*', '#26804b')):
            fmt = QtGui.QTextCharFormat()
            fmt.setForeground(QtGui.QColor(color))
            for match in re.finditer(pattern, text):
                self.setFormat(match.start(), match.end()-match.start(), fmt)


class PlcBrowser(QtWidgets.QWidget):
    def __init__(self, prefix, index, demo=False, on_plot=None):
        super().__init__()
        self.prefix, self.index, self.demo = prefix, index, demo
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.variables = []
        layout = QtWidgets.QVBoxLayout(self)
        self.refresh = QtWidgets.QPushButton('Load compiled PLC source')
        self.refresh.clicked.connect(self._load)
        layout.addWidget(self.refresh)
        self.status = QtWidgets.QLabel('Compile-time metadata only; variable values are not sampled.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.source = QtWidgets.QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.highlighter = PlcHighlighter(self.source.document())
        layout.addWidget(self.source, 2)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search variable names or scope')
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QtWidgets.QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(('Scope', 'Registered variable'))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)
        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._poll)
        # A completed worker never calls Qt; destroying a tab cannot deliver into it.
        executor = self.executor
        weakref.finalize(self, executor.shutdown, wait=False, cancel_futures=True)

    def _load(self):
        if self.future is not None:
            return
        self.refresh.setEnabled(False)
        self.source.clear()
        self.variables = []
        self._filter()
        self.status.setText('Loading…')
        if self.demo:
            self.source.setPlainText('// DEMO compiled source\nvar counter := 0;\ncounter := counter + 1;')
            self.variables = [('Local', 'counter')]
            self._filter()
            self.status.setText('DEMO · compile-time metadata')
            self.refresh.setEnabled(True)
            return
        self.future = self.executor.submit(fetch_metadata, self.prefix, self.index)
        self.timer.start()

    def _poll(self):
        if self.future is None or not self.future.done():
            return
        self.timer.stop()
        try:
            source, self.variables = self.future.result()
            self.source.setPlainText(source)
            self._filter()
            self.status.setText(f'{len(self.variables)} registered variables · compile-time metadata')
        except Exception as ex:
            self.status.setText(str(ex))
        finally:
            self.future = None
            self.refresh.setEnabled(True)

    def _filter(self):
        query = self.search.text().casefold()
        rows = [row for row in self.variables if query in ' '.join(row).casefold()]
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for j, value in enumerate(row):
                self.table.setItem(i, j, QtWidgets.QTableWidgetItem(value))
