"""Reusable Qt series plot with selection, zoom and pan."""
import math
from ecmc_stream_qt import compact_float_text
from qt_compat import QtCore, QtGui, QtWidgets

Signal = getattr(QtCore, "pyqtSignal", None) or QtCore.Signal

PLOT_COLORS = [
    "#2563eb",
    "#dc2626",
    "#059669",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#be123c",
    "#4f46e5",
]

class SeriesPlotWidget(QtWidgets.QWidget):
    range_changed = Signal(float, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._series = {}
        self._title = ""
        self._x_label = ""
        self._y_label = ""
        self._empty_text = "No data"
        self._x_floor = None
        self._y_floor = None
        self._selection_enabled = False
        self._selection_range = None
        self._active_cursor = None
        self._pan_active = False
        self._pan_last_pos = None
        self._plot_rect = QtCore.QRectF()
        self._plot_x_min = 0.0
        self._plot_x_max = 1.0
        self._plot_y_min = 0.0
        self._plot_y_max = 1.0
        self._full_x_min = 0.0
        self._full_x_max = 1.0
        self._full_y_min = 0.0
        self._full_y_max = 1.0
        self._view_x_range = None
        self._view_y_range = None
        self.setMinimumSize(700, 420)

    def set_axes(self, title="", x_label="", y_label="", empty_text="No data"):
        self._title = str(title or "")
        self._x_label = str(x_label or "")
        self._y_label = str(y_label or "")
        self._empty_text = str(empty_text or "No data")
        self.update()

    def set_x_floor(self, value):
        self._x_floor = None if value is None else float(value)
        self.update()

    def set_y_floor(self, value):
        self._y_floor = None if value is None else float(value)
        self.update()

    def set_series(self, series):
        self._series = dict(series or {})
        self.update()

    def set_selection_enabled(self, enabled):
        self._selection_enabled = bool(enabled)
        if not self._selection_enabled:
            self._active_cursor = None
        self.update()

    def set_selection_range(self, start_s, end_s):
        if start_s is None or end_s is None:
            self._selection_range = None
        else:
            start = float(start_s)
            end = float(end_s)
            self._selection_range = (min(start, end), max(start, end))
        self.update()

    def selection_range(self):
        return self._selection_range

    def reset_zoom(self):
        self._view_x_range = None
        self._view_y_range = None
        self._pan_active = False
        self._pan_last_pos = None
        self.unsetCursor()
        self.update()

    def view_state(self):
        return {
            "x_range": list(self._view_x_range) if self._view_x_range is not None else None,
            "y_range": list(self._view_y_range) if self._view_y_range is not None else None,
        }

    def set_view_state(self, state):
        payload = dict(state or {})
        x_range = payload.get("x_range")
        y_range = payload.get("y_range")
        self._view_x_range = None if x_range is None else (float(x_range[0]), float(x_range[1]))
        self._view_y_range = None if y_range is None else (float(y_range[0]), float(y_range[1]))
        self.update()

    def _map_plot_x(self, value):
        return self._plot_rect.left() + ((float(value) - self._plot_x_min) / (self._plot_x_max - self._plot_x_min)) * self._plot_rect.width()

    def _map_value_x(self, pos_x):
        if self._plot_rect.width() <= 0.0:
            return self._plot_x_min
        ratio = (float(pos_x) - self._plot_rect.left()) / self._plot_rect.width()
        ratio = max(0.0, min(1.0, ratio))
        return self._plot_x_min + (ratio * (self._plot_x_max - self._plot_x_min))

    def _map_value_y(self, pos_y):
        if self._plot_rect.height() <= 0.0:
            return self._plot_y_min
        ratio = (self._plot_rect.bottom() - float(pos_y)) / self._plot_rect.height()
        ratio = max(0.0, min(1.0, ratio))
        return self._plot_y_min + (ratio * (self._plot_y_max - self._plot_y_min))

    def _normalize_view_range(self, view_range, full_min, full_max):
        if view_range is None:
            return None
        if full_max <= full_min:
            return None
        start, end = float(view_range[0]), float(view_range[1])
        if end < start:
            start, end = end, start
        full_span = full_max - full_min
        min_span = max(full_span * 1e-6, 1e-9)
        span = max(min_span, end - start)
        if span >= full_span * 0.999999:
            return None
        if start < full_min:
            end += full_min - start
            start = full_min
        if end > full_max:
            start -= end - full_max
            end = full_max
        start = max(full_min, start)
        end = min(full_max, end)
        if end - start < min_span:
            return None
        return (start, end)

    def _zoom_axis(self, axis, anchor_value, factor):
        if axis == "x":
            current_min, current_max = self._plot_x_min, self._plot_x_max
            full_min, full_max = self._full_x_min, self._full_x_max
        else:
            current_min, current_max = self._plot_y_min, self._plot_y_max
            full_min, full_max = self._full_y_min, self._full_y_max
        current_span = current_max - current_min
        full_span = full_max - full_min
        if current_span <= 0.0 or full_span <= 0.0:
            return
        new_span = current_span * float(factor)
        if new_span >= full_span * 0.999999:
            new_range = None
        else:
            rel = (float(anchor_value) - current_min) / current_span
            rel = max(0.0, min(1.0, rel))
            start = float(anchor_value) - (rel * new_span)
            end = start + new_span
            new_range = self._normalize_view_range((start, end), full_min, full_max)
        if axis == "x":
            self._view_x_range = new_range
        else:
            self._view_y_range = new_range
        self.update()

    def _pan_by_pixels(self, delta_x, delta_y):
        if self._plot_rect.width() <= 0.0 or self._plot_rect.height() <= 0.0:
            return
        if self._view_x_range is not None:
            span = self._view_x_range[1] - self._view_x_range[0]
            shift = -(float(delta_x) / self._plot_rect.width()) * span
            self._view_x_range = self._normalize_view_range(
                (self._view_x_range[0] + shift, self._view_x_range[1] + shift),
                self._full_x_min,
                self._full_x_max,
            )
        if self._view_y_range is not None:
            span = self._view_y_range[1] - self._view_y_range[0]
            shift = (float(delta_y) / self._plot_rect.height()) * span
            self._view_y_range = self._normalize_view_range(
                (self._view_y_range[0] + shift, self._view_y_range[1] + shift),
                self._full_y_min,
                self._full_y_max,
            )
        self.update()

    def paintEvent(self, _event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QtGui.QColor("#fbfbfc"))

        margin_l = 68
        margin_r = 24
        margin_t = 42
        margin_b = 52
        plot = rect.adjusted(margin_l, margin_t, -margin_r, -margin_b)
        if plot.width() <= 10 or plot.height() <= 10:
            return

        painter.setPen(QtGui.QPen(QtGui.QColor("#d1d5db"), 1))
        painter.drawRect(plot)

        visible = []
        for name, points in self._series.items():
            clean = []
            for x_val, y_val in points or []:
                try:
                    xf = float(x_val)
                    yf = float(y_val)
                except Exception:
                    continue
                if not math.isfinite(xf) or not math.isfinite(yf):
                    continue
                clean.append((xf, yf))
            if clean:
                visible.append((str(name), clean))

        painter.setPen(QtGui.QColor("#111827"))
        painter.setFont(QtGui.QFont(painter.font().family(), 11, QtGui.QFont.Bold))
        painter.drawText(plot.left(), 24, self._title)

        if not visible:
            painter.setPen(QtGui.QColor("#6b7280"))
            painter.setFont(QtGui.QFont(painter.font().family(), 10))
            painter.drawText(plot, QtCore.Qt.AlignCenter, self._empty_text)
            return

        all_x = [x_val for _name, points in visible for x_val, _y_val in points]
        all_y = [y_val for _name, points in visible for _x_val, y_val in points]
        x_min = min(all_x)
        x_max = max(all_x)
        y_min = min(all_y)
        y_max = max(all_y)
        if self._x_floor is not None:
            x_min = min(x_min, self._x_floor)
        if self._y_floor is not None:
            y_min = min(y_min, self._y_floor)
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            pad = max(abs(y_min) * 0.1, 1.0)
            y_min -= pad
            y_max += pad
        else:
            pad = max((y_max - y_min) * 0.08, 1e-12)
            y_min -= pad
            y_max += pad
            if self._y_floor is not None:
                y_min = min(y_min, self._y_floor)

        self._full_x_min = float(x_min)
        self._full_x_max = float(x_max)
        self._full_y_min = float(y_min)
        self._full_y_max = float(y_max)
        self._view_x_range = self._normalize_view_range(self._view_x_range, self._full_x_min, self._full_x_max)
        self._view_y_range = self._normalize_view_range(self._view_y_range, self._full_y_min, self._full_y_max)
        if self._view_x_range is not None:
            x_min, x_max = self._view_x_range
        if self._view_y_range is not None:
            y_min, y_max = self._view_y_range

        def map_x(value):
            return plot.left() + ((float(value) - x_min) / (x_max - x_min)) * plot.width()

        def map_y(value):
            return plot.bottom() - ((float(value) - y_min) / (y_max - y_min)) * plot.height()

        self._plot_rect = QtCore.QRectF(plot)
        self._plot_x_min = float(x_min)
        self._plot_x_max = float(x_max)
        self._plot_y_min = float(y_min)
        self._plot_y_max = float(y_max)

        grid_pen = QtGui.QPen(QtGui.QColor("#e5e7eb"), 1)
        for idx in range(1, 5):
            x_pos = plot.left() + (plot.width() * idx) / 5.0
            y_pos = plot.top() + (plot.height() * idx) / 5.0
            painter.setPen(grid_pen)
            painter.drawLine(int(x_pos), int(plot.top()), int(x_pos), int(plot.bottom()))
            painter.drawLine(int(plot.left()), int(y_pos), int(plot.right()), int(y_pos))

        painter.setPen(QtGui.QColor("#374151"))
        painter.setFont(QtGui.QFont(painter.font().family(), 9))
        painter.drawText(6, int(plot.top()) + 6, compact_float_text(y_max))
        painter.drawText(6, int(plot.bottom()) + 4, compact_float_text(y_min))
        painter.drawText(int(plot.left()), rect.bottom() - 10, compact_float_text(x_min))
        painter.drawText(int(plot.right()) - 60, rect.bottom() - 10, compact_float_text(x_max))
        if self._x_label:
            painter.drawText(plot.center().x() - 40, rect.bottom() - 10, self._x_label)
        if self._y_label:
            painter.save()
            painter.translate(16, plot.center().y() + 40)
            painter.rotate(-90)
            painter.drawText(0, 0, self._y_label)
            painter.restore()

        legend_x = plot.left()
        legend_y = 34
        for idx, (name, _points) in enumerate(visible):
            color = QtGui.QColor(PLOT_COLORS[idx % len(PLOT_COLORS)])
            painter.setBrush(color)
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawEllipse(QtCore.QPointF(legend_x + 6, legend_y - 4), 4.0, 4.0)
            painter.setPen(QtGui.QColor("#1f2937"))
            painter.drawText(int(legend_x + 16), int(legend_y), name)
            legend_x += max(120, 18 + painter.fontMetrics().horizontalAdvance(name) + 22)

        for idx, (_name, points) in enumerate(visible):
            color = QtGui.QColor(PLOT_COLORS[idx % len(PLOT_COLORS)])
            path = QtGui.QPainterPath()
            for point_idx, (x_val, y_val) in enumerate(points):
                qpoint = QtCore.QPointF(map_x(x_val), map_y(y_val))
                if point_idx == 0:
                    path.moveTo(qpoint)
                else:
                    path.lineTo(qpoint)
            painter.setPen(QtGui.QPen(color, 2))
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.drawPath(path)
            last_x, last_y = points[-1]
            painter.setBrush(color)
            painter.drawEllipse(QtCore.QPointF(map_x(last_x), map_y(last_y)), 3.0, 3.0)

        if self._selection_enabled and self._selection_range is not None:
            start_sel, end_sel = self._selection_range
            start_sel = max(x_min, min(x_max, float(start_sel)))
            end_sel = max(x_min, min(x_max, float(end_sel)))
            if end_sel < start_sel:
                start_sel, end_sel = end_sel, start_sel
            left_sel = map_x(start_sel)
            right_sel = map_x(end_sel)
            shade = QtGui.QColor(15, 23, 42, 20)
            painter.fillRect(QtCore.QRectF(plot.left(), plot.top(), max(0.0, left_sel - plot.left()), plot.height()), shade)
            painter.fillRect(QtCore.QRectF(right_sel, plot.top(), max(0.0, plot.right() - right_sel), plot.height()), shade)

            start_pen = QtGui.QPen(QtGui.QColor("#0f766e"), 2, QtCore.Qt.DashLine)
            end_pen = QtGui.QPen(QtGui.QColor("#b45309"), 2, QtCore.Qt.DashLine)
            painter.setPen(start_pen)
            painter.drawLine(QtCore.QPointF(left_sel, plot.top()), QtCore.QPointF(left_sel, plot.bottom()))
            painter.setPen(end_pen)
            painter.drawLine(QtCore.QPointF(right_sel, plot.top()), QtCore.QPointF(right_sel, plot.bottom()))

            painter.setPen(QtGui.QColor("#0f172a"))
            painter.setBrush(QtGui.QColor("#ffffff"))
            painter.drawText(int(left_sel + 4), int(plot.top()) + 14, f"S {compact_float_text(start_sel)}")
            painter.drawText(int(right_sel + 4), int(plot.top()) + 28, f"E {compact_float_text(end_sel)}")

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.RightButton and self._plot_rect.contains(event.pos()):
            self._pan_active = True
            self._pan_last_pos = QtCore.QPointF(event.pos())
            self.setCursor(QtCore.Qt.ClosedHandCursor)
            event.accept()
            return
        if not self._selection_enabled or self._selection_range is None or not self._plot_rect.contains(event.pos()):
            return super().mousePressEvent(event)
        start_sel, end_sel = self._selection_range
        x_pos = float(event.pos().x())
        start_px = self._map_plot_x(start_sel)
        end_px = self._map_plot_x(end_sel)
        if abs(x_pos - start_px) <= abs(x_pos - end_px):
            self._active_cursor = "start"
        else:
            self._active_cursor = "end"
        self._move_cursor_to_pos(x_pos)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._pan_active and self._pan_last_pos is not None:
            delta = QtCore.QPointF(event.pos()) - self._pan_last_pos
            self._pan_last_pos = QtCore.QPointF(event.pos())
            self._pan_by_pixels(delta.x(), delta.y())
            event.accept()
            return
        if self._active_cursor is None or not self._selection_enabled:
            return super().mouseMoveEvent(event)
        self._move_cursor_to_pos(float(event.pos().x()))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.RightButton and self._pan_active:
            self._pan_active = False
            self._pan_last_pos = None
            self.unsetCursor()
            event.accept()
            return
        if self._active_cursor is None:
            return super().mouseReleaseEvent(event)
        self._move_cursor_to_pos(float(event.pos().x()))
        self._active_cursor = None
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.RightButton:
            self.reset_zoom()
            event.accept()
            return
        if not self._selection_enabled or not self._plot_rect.contains(event.pos()):
            return super().mouseDoubleClickEvent(event)
        self._selection_range = (self._full_x_min, self._full_x_max)
        self.range_changed.emit(float(self._full_x_min), float(self._full_x_max))
        self.update()
        event.accept()

    def wheelEvent(self, event):
        pos = event.position() if hasattr(event, "position") else QtCore.QPointF(event.pos())
        if not self._plot_rect.contains(pos):
            return super().wheelEvent(event)
        delta = event.angleDelta().y()
        if delta == 0:
            return super().wheelEvent(event)
        factor = 0.85 if delta > 0 else 1.18
        modifiers = event.modifiers()
        if modifiers & QtCore.Qt.ControlModifier:
            self._zoom_axis("x", self._map_value_x(pos.x()), factor)
            self._zoom_axis("y", self._map_value_y(pos.y()), factor)
        elif modifiers & QtCore.Qt.ShiftModifier:
            self._zoom_axis("y", self._map_value_y(pos.y()), factor)
        else:
            self._zoom_axis("x", self._map_value_x(pos.x()), factor)
        event.accept()

    def _move_cursor_to_pos(self, pos_x):
        if self._selection_range is None or self._active_cursor is None:
            return
        value = self._map_value_x(pos_x)
        start_sel, end_sel = self._selection_range
        if self._active_cursor == "start":
            start_sel = min(value, end_sel)
        else:
            end_sel = max(value, start_sel)
        self._selection_range = (float(start_sel), float(end_sel))
        self.range_changed.emit(float(start_sel), float(end_sel))
        self.update()


