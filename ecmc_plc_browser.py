import weakref
"""Native source and variable browser for compile-time PLC metadata."""
import re
import time
from concurrent.futures import ThreadPoolExecutor
from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_plc_metadata import fetch_metadata, fetch_values, registered_index_at


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
        self.watched = set()
        self.page_times = {}
        self.pending_pages = []
        self.active_page = None
        self.hover_index = None
        self.hover_pos = None
        self.next_request = 0.0
        self.hover_timer = QtCore.QTimer(self)
        self.hover_timer.setSingleShot(True)
        self.hover_timer.setInterval(450)
        self.hover_timer.timeout.connect(self._read_hover)
        layout = QtWidgets.QVBoxLayout(self)
        self.refresh = QtWidgets.QPushButton('Load compiled PLC source')
        self.refresh.clicked.connect(self._load)
        layout.addWidget(self.refresh)
        self.read_values = QtWidgets.QPushButton('Refresh watch list')
        self.read_values.setEnabled(False)
        self.read_values.clicked.connect(self._load_values)
        layout.addWidget(self.read_values)
        self.status = QtWidgets.QLabel('Load source, then hover or right-click a registered variable.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.source = QtWidgets.QPlainTextEdit()
        self.source.setReadOnly(True)
        self.source.viewport().setMouseTracking(True)
        self.source.viewport().installEventFilter(self)
        self.source.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.source.customContextMenuRequested.connect(self._source_menu)
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
        self.search.setPlaceholderText('Find a registered variable to watch')
        self.search.returnPressed.connect(self._search_add)
        layout.addWidget(QtWidgets.QLabel('Watch list'))
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
        self.watched.clear()
        self.page_times.clear()
        self.pending_pages.clear()
        self.hover_index = None
        self.hover_timer.stop()
        QtWidgets.QToolTip.hideText()
        self.read_values.setEnabled(False)
        self._filter()
        self.status.setText('Loading…')
        if self.demo:
            self.source.setPlainText('// DEMO compiled source\nvar counter := 0;\ncounter := counter + 1;')
            self.variables = [('Local', 'counter')]
            self._filter()
            self.status.setText('DEMO · compile-time metadata')
            self.refresh.setEnabled(True)
            self.read_values.setEnabled(False)
            return
        self.next_request = time.monotonic() + 1.1
        self.future = self.executor.submit(fetch_metadata, self.prefix, self.index)
        self.timer.start()

    def _index_at(self, pos):
        cursor = self.source.cursorForPosition(pos)
        block = cursor.block()
        # Qt cursor offsets are UTF-16; convert within the current block.
        offset = cursor.position() - block.position()
        line = block.text()
        offset = len(line.encode('utf-16-le')[:offset*2].decode('utf-16-le', errors='ignore'))
        return registered_index_at(line, offset, self.variables)

    def eventFilter(self, obj, event):
        if obj is self.source.viewport():
            if event.type() == QtCore.QEvent.MouseMove:
                index = self._index_at(event.pos())
                if index != self.hover_index:
                    self.hover_timer.stop()
                    QtWidgets.QToolTip.hideText()
                    self.hover_index = index
                    if index is not None:
                        self.hover_pos = self.source.viewport().mapToGlobal(event.pos())
                        self.hover_timer.start()
            elif event.type() == QtCore.QEvent.Leave:
                self.hover_index = None
                self.hover_timer.stop()
                QtWidgets.QToolTip.hideText()
        return super().eventFilter(obj, event)

    def _show_hover(self):
        index = self.hover_index
        if index is None or index not in self.values or self.hover_pos is None:
            return
        scope, name = self.variables[index]
        QtWidgets.QToolTip.showText(self.hover_pos,
            f'{name} ({scope}) = {self.values[index]:.12g} [snapshot]', self.source)

    def _read_hover(self):
        index = self.hover_index
        if index is None or not self.isVisible():
            return
        if time.monotonic() - self.page_times.get(index//32, -100) < 2:
            self._show_hover()
        elif self.future is None and not self.pending_pages and time.monotonic() >= self.next_request:
            self._queue_page(index//32)

    def _source_menu(self, pos):
        index = self._index_at(pos)
        menu = self.source.createStandardContextMenu()
        if index is not None:
            name = self.variables[index][1]
            menu.addSeparator()
            menu.addAction('Read ' + name, lambda: self._inspect(index))
            menu.addAction('Add to watch list', lambda: self._watch(index))
            menu.addAction('Highlight occurrences', lambda: self._highlight_variable(name))
            menu.addAction('Copy variable name', lambda: QtWidgets.QApplication.clipboard().setText(name))
        execute = getattr(menu, 'exec', None) or menu.exec_
        execute(self.source.viewport().mapToGlobal(pos))

    def _inspect(self, index):
        self.hover_index = index
        self.hover_pos = QtGui.QCursor.pos()
        if index in self.values:
            self._show_hover()
        self._queue_page(index//32)

    def _watch(self, index):
        self.watched.add(index)
        self._filter()
        self._queue_page(index//32)

    def _search_add(self):
        query = self.search.text().strip().casefold()
        if not query:
            return
        matches = [(i, f'{scope}: {name}') for i,(scope,name) in enumerate(self.variables)
                   if query in name.casefold()]
        if not matches:
            self.status.setText('No matching registered variable')
            return
        labels = [label for _,label in matches]
        label, ok = QtWidgets.QInputDialog.getItem(self, 'Add watch', 'Variable', labels, 0, False)
        if ok:
            self._watch(matches[labels.index(label)][0])
            self.search.clear()

    def _load_values(self):
        for page in sorted({i//32 for i in self.watched}):
            self._queue_page(page)

    def _queue_page(self, page):
        if self.demo:
            for i in range(page*32, min((page+1)*32, len(self.variables))):
                self.values[i] = self.values.get(i, 0)+1
            self.page_times[page] = time.monotonic()
            self._filter()
            self._show_hover()
            return
        if page != self.active_page and page not in self.pending_pages:
            self.pending_pages.append(page)
        self.timer.start()
        self._start_page()

    def _start_page(self):
        if self.future is not None or not self.pending_pages or time.monotonic() < self.next_request:
            return
        self.active_page = self.pending_pages.pop(0)
        self.next_request = time.monotonic()+1.1
        self._fetching_values = True
        self.refresh.setEnabled(False)
        self.read_values.setEnabled(False)
        self.status.setText('Requesting bounded PLC snapshot…')
        self.future = self.executor.submit(fetch_values, self.prefix, self.index, self.active_page)

    def _poll(self):
        if self.future is None:
            self._start_page()
            if self.future is None and not self.pending_pages:
                self.timer.stop()
            return
        if not self.future.done():
            return
        try:
            if self._fetching_values:
                self.values.update(self.future.result())
                self.page_times[self.active_page] = time.monotonic()
                self._filter()
                if self.hover_index is not None and self.hover_index//32 == self.active_page:
                    self._show_hover()
                self.status.setText('Snapshot received · values are not continuously updated')
            else:
                source, self.variables = self.future.result()
                self.source.setPlainText(source)
                self._filter()
                self.status.setText(f'{len(self.variables)} registered variables · hover or right-click source')
        except Exception as ex:
            self.status.setText(str(ex))
        finally:
            self.future = None
            self._fetching_values = False
            self.active_page = None
            self.refresh.setEnabled(not self.pending_pages)
            self.read_values.setEnabled(bool(self.watched) and not self.pending_pages)

    def _filter(self):
        self.table.setRowCount(len(self.watched))
        for row, index in enumerate(sorted(self.watched)):
            for col, value in enumerate(self.variables[index]):
                item = QtWidgets.QTableWidgetItem(value)
                item.setData(QtCore.Qt.UserRole, index)
                self.table.setItem(row, col, item)
            value = self.values.get(index)
            self.table.setItem(row, 2, QtWidgets.QTableWidgetItem('—' if value is None else format(value,'.12g')))
        self.read_values.setEnabled(bool(self.watched) and self.future is None and not self.pending_pages)

    def _selected_variable(self):
        row = self.table.currentRow()
        item = self.table.item(row, 1) if row >= 0 else None
        return item.text() if item is not None else ""

    def _highlight_variable(self, name=None):
        name = name or self._selected_variable()
        selections = []
        if name:
            pattern = r"(?<![\w.])" + re.escape(name) + r"(?![\w.])"
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
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        self.table.selectRow(row)
        name = self._selected_variable()
        index = self.table.item(row,0).data(QtCore.Qt.UserRole)
        menu = QtWidgets.QMenu(self)
        menu.addAction("Remove from watch list", lambda: self._remove_watch(index))
        menu.addAction("Copy variable name", lambda: QtWidgets.QApplication.clipboard().setText(name))
        if self.on_plot and not self.demo:
            menu.addAction("Trend existing EPICS PV…", lambda: self._trend_pv(name, False))
            menu.addAction("Add existing EPICS PV to trend…", lambda: self._trend_pv(name, True))
        execute = getattr(menu, "exec", None) or menu.exec_
        execute(self.table.viewport().mapToGlobal(pos))

    def _remove_watch(self, index):
        self.watched.discard(index)
        self._filter()

    def _trend_pv(self, name, add):
        pv, ok = QtWidgets.QInputDialog.getText(self, "Existing EPICS PV",
            "Full PV name for " + name + " (no PLC sampling):")
        if ok and re.fullmatch(r"[A-Za-z0-9_:.\\-]+", pv.strip()):
            self.on_plot([pv.strip()], add=add)
