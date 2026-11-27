import weakref
"""Native source and variable browser for compile-time PLC metadata."""
import re
from concurrent.futures import ThreadPoolExecutor
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_plc_metadata import fetch_metadata, fetch_values


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
        self.on_plot = on_plot
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.variables = []
        self.values = {}
        self._fetching_values = False
        layout = QtWidgets.QVBoxLayout(self)
        self.refresh = QtWidgets.QPushButton('Load compiled PLC source')
        self.refresh.clicked.connect(self._load)
        layout.addWidget(self.refresh)
        self.read_values = QtWidgets.QPushButton('Refresh selected variable page (up to 32)')
        self.read_values.setEnabled(False)
        self.read_values.clicked.connect(self._load_values)
        layout.addWidget(self.read_values)
        self.status = QtWidgets.QLabel('Load source, then select a variable and refresh its page.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.source = QtWidgets.QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.setFont(QtGui.QFontDatabase.systemFont(QtGui.QFontDatabase.FixedFont))
        self.highlighter = PlcHighlighter(self.source.document())
        layout.addWidget(self.source, 2)
        source_actions = QtWidgets.QHBoxLayout()
        export = QtWidgets.QPushButton("Export source")
        export.clicked.connect(self._export_source)
        source_actions.addWidget(export)
        source_actions.addStretch()
        layout.addLayout(source_actions)
        self.search = QtWidgets.QLineEdit()
        self.search.setPlaceholderText('Search variable names or scope')
        self.search.textChanged.connect(self._filter)
        layout.addWidget(self.search)
        self.table = QtWidgets.QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(('Scope', 'Registered variable', 'Snapshot value'))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self._highlight_variable)
        self.table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._variable_menu)
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
        self.values.clear()
        self.read_values.setEnabled(False)
        self._filter()
        self.status.setText('Loading…')
        if self.demo:
            self.source.setPlainText('// DEMO compiled source\nvar counter := 0;\ncounter := counter + 1;')
            self.variables = [('Local', 'counter')]
            self._filter()
            self.status.setText('DEMO · compile-time metadata')
            self.refresh.setEnabled(True)
            self.read_values.setEnabled(True)
            return
        self.future = self.executor.submit(fetch_metadata, self.prefix, self.index)
        self.timer.start()

    def _load_values(self):
        if self.future is not None or not self.variables:
            return
        row = self.table.currentRow()
        item = self.table.item(row, 0) if row >= 0 else None
        page = int(item.data(QtCore.Qt.UserRole))//32 if item is not None else 0
        if self.demo:
            self.values[0] = self.values.get(0, 0)+1
            self._filter()
            return
        self._fetching_values = True
        self.refresh.setEnabled(False)
        self.read_values.setEnabled(False)
        self.status.setText('Requesting bounded PLC snapshot…')
        self.future = self.executor.submit(fetch_values,self.prefix,self.index,page)
        self.timer.start()

    def _poll(self):
        if self.future is None or not self.future.done():
            return
        self.timer.stop()
        try:
            if self._fetching_values:
                self.values = self.future.result()
                self._filter()
                self.status.setText(f'{len(self.values)} values sampled in one PLC execution')
                return
            source, self.variables = self.future.result()
            self.source.setPlainText(source)
            self._filter()
            self.status.setText(f'{len(self.variables)} registered variables · compile-time metadata')
        except Exception as ex:
            self.status.setText(str(ex))
        finally:
            self.future = None
            self._fetching_values = False
            self.refresh.setEnabled(True)
            self.read_values.setEnabled(bool(self.variables))

    def _filter(self):
        query = self.search.text().casefold()
        rows = [(index,row) for index,row in enumerate(self.variables) if query in ' '.join(row).casefold()]
        self.table.setRowCount(len(rows))
        for i, (index,row) in enumerate(rows):
            for j, value in enumerate(row):
                item=QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole,index)
                self.table.setItem(i,j,item)
            value=self.values.get(index)
            self.table.setItem(i,2,QtWidgets.QTableWidgetItem('—' if value is None else format(value,'.12g')))

    def _selected_variable(self):
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        return item.text() if item is not None else ""

    def _highlight_variable(self):
        name = self._selected_variable()
        selections = []
        if name:
            pattern = r"(?<![\\w.])" + re.escape(name) + r"(?![\\w.])"
            for match in re.finditer(pattern, self.source.toPlainText()):
                cursor = QtGui.QTextCursor(self.source.document())
                # Qt positions are UTF-16 offsets, unlike Python string offsets.
                source = self.source.toPlainText()
                cursor.setPosition(len(source[:match.start()].encode("utf-16-le")) // 2)
                cursor.setPosition(len(source[:match.end()].encode("utf-16-le")) // 2, QtGui.QTextCursor.KeepAnchor)
                selection = QtWidgets.QTextEdit.ExtraSelection()
                selection.cursor = cursor
                selection.format.setBackground(QtGui.QColor("#e7b94e"))
                selection.format.setForeground(QtGui.QColor("#202020"))
                selections.append(selection)
        self.source.setExtraSelections(selections)
        if selections:
            self.source.setTextCursor(selections[0].cursor)
            self.source.ensureCursorVisible()

    def _export_source(self):
        from pathlib import Path
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export compiled PLC source", f"PLC{self.index:02d}.plc")
        if path:
            try:
                Path(path).write_text(self.source.toPlainText(), encoding="utf-8")
            except OSError as exc:
                QtWidgets.QMessageBox.warning(self, "Export failed", str(exc))

    def _variable_menu(self, pos):
        name = self._selected_variable()
        if not name:
            return
        menu = QtWidgets.QMenu(self)
        menu.addAction("Copy variable name", lambda: QtWidgets.QApplication.clipboard().setText(name))
        if self.on_plot and not self.demo:
            menu.addAction("Trend existing EPICS PV…", lambda: self._trend_pv(name, False))
            menu.addAction("Add existing EPICS PV to trend…", lambda: self._trend_pv(name, True))
        execute = getattr(menu, "exec", None) or menu.exec_
        execute(self.table.viewport().mapToGlobal(pos))

    def _trend_pv(self, name, add):
        pv, ok = QtWidgets.QInputDialog.getText(self, "Existing EPICS PV",
            "Full PV name for " + name + " (no PLC sampling):")
        if ok and re.fullmatch(r"[A-Za-z0-9_:.\\-]+", pv.strip()):
            self.on_plot([pv.strip()], add=add)
