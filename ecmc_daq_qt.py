#!/usr/bin/env python3
import argparse
import json
import math
from ecmc_caqtdm import launch_caqtdm
import subprocess
import sys
import time
from collections import deque
from ecmc_daq_trigger import TriggerCapture
from ecmc_acquisition import sanitize_samples as _sanitize_samples, finite_value
from ecmc_plot_widgets import SeriesPlotWidget
from pathlib import Path

from qt_compat import QtCore, QtGui, QtWidgets
from ecmc_settings import UiSettings
from ecmc_widgets import reset_combo

from ecmc_epics import EpicsClient, _join_prefix_pv
from ecmc_formatting import compact_float_text
from ecmc_widgets import CompactDoubleSpinBox


Signal = getattr(QtCore, "pyqtSignal", None)
if Signal is None:
    Signal = getattr(QtCore, "Signal")

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
APP_LAUNCH_PLACEHOLDER = "Open app..."
APP_LAUNCH_DAQ = "New DAQ App"
APP_LAUNCH_STREAM = "Stream App"
APP_LAUNCH_AXIS = "Axis Cfg App"
APP_LAUNCH_CONTROLLER = "Cntrl Cfg App"
APP_LAUNCH_MOTION = "Motion App"
APP_LAUNCH_ISO230 = "ISO230 App"
APP_LAUNCH_RTLOG = "ecmc Log App"
APP_LAUNCH_CAQTDM_MAIN = "caqtdm Main"


def _coerce_float(value):
    try:
        return float(str(value).strip())
    except Exception:
        return None


def _median(values):
    vals = sorted(float(v) for v in values)
    if not vals:
        return None
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return 0.5 * (vals[mid - 1] + vals[mid])


def motion_sample_metrics(samples):
    """Raw selected event statistics; no interpolation or inferred missing samples."""
    clean=_sanitize_samples(samples)
    if not clean:
        return {}
    values=[v for _,v in clean]
    mean=math.fsum(values)/len(values)
    result={"mean":mean,"std":math.sqrt(math.fsum((v-mean)**2 for v in values)/len(values)),
            "rms":math.sqrt(math.fsum(v*v for v in values)/len(values)),
            "pp":max(values)-min(values),"count":len(values)}
    if len(clean)>1:
        intervals=[b[0]-a[0] for a,b in zip(clean,clean[1:]) if b[0]>a[0]]
        if intervals:
            dt=_median(intervals)
            result.update(rate=1/dt,event_rate=len(intervals)/math.fsum(intervals),
                jitter_ms=1000*math.sqrt(math.fsum((d-dt)**2 for d in intervals)/len(intervals)),
                gaps=sum(d>2*dt for d in intervals),max_gap_ms=1000*max(intervals))
    return result


def _largest_power_of_two_leq(value):
    n = int(value or 0)
    if n < 1:
        return 0
    return 1 << (n.bit_length() - 1)


def _resample_uniform(samples):
    clean = _sanitize_samples(samples)
    if len(clean) < 3:
        return {
            "clean": clean,
            "uniform": [],
            "sample_rate_hz": None,
            "median_dt_s": None,
            "span_s": 0.0,
        }

    diffs = [clean[idx][0] - clean[idx - 1][0] for idx in range(1, len(clean))]
    diffs = [dt for dt in diffs if dt > 0.0 and math.isfinite(dt)]
    median_dt = _median(diffs)
    if median_dt is None or median_dt <= 0.0:
        return {
            "clean": clean,
            "uniform": [],
            "sample_rate_hz": None,
            "median_dt_s": None,
            "span_s": max(0.0, clean[-1][0] - clean[0][0]),
        }

    start = clean[0][0]
    stop = clean[-1][0]
    span = max(0.0, stop - start)
    count = max(2, int(math.floor(span / median_dt)) + 1)
    uniform_times = [start + (idx * median_dt) for idx in range(count)]
    if uniform_times[-1] < stop:
        uniform_times.append(stop)

    uniform_values = []
    src_index = 0
    for target_ts in uniform_times:
        while src_index + 1 < len(clean) and clean[src_index + 1][0] < target_ts:
            src_index += 1
        if src_index + 1 >= len(clean):
            uniform_values.append(clean[-1][1])
            continue
        left_ts, left_val = clean[src_index]
        right_ts, right_val = clean[src_index + 1]
        if target_ts <= left_ts or abs(right_ts - left_ts) < 1e-12:
            uniform_values.append(left_val)
            continue
        ratio = (target_ts - left_ts) / (right_ts - left_ts)
        uniform_values.append(left_val + ((right_val - left_val) * ratio))

    uniform = list(zip(uniform_times, uniform_values))
    return {
        "clean": clean,
        "uniform": uniform,
        "sample_rate_hz": 1.0 / median_dt if median_dt > 0.0 else None,
        "median_dt_s": median_dt,
        "span_s": span,
    }


def _fft_complex(values):
    n = len(values)
    data = list(values)
    j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit:
            j ^= bit
            bit >>= 1
        j ^= bit
        if i < j:
            data[i], data[j] = data[j], data[i]

    length = 2
    while length <= n:
        angle = -2.0 * math.pi / float(length)
        step = complex(math.cos(angle), math.sin(angle))
        half = length // 2
        for start in range(0, n, length):
            factor = 1.0 + 0.0j
            for offset in range(half):
                even = data[start + offset]
                odd = factor * data[start + offset + half]
                data[start + offset] = even + odd
                data[start + offset + half] = even - odd
                factor *= step
        length <<= 1
    return data


def _slice_samples_by_relative_time(samples, origin_ts, start_s=None, end_s=None):
    if not samples:
        return []
    start_value = None if start_s is None else float(start_s)
    end_value = None if end_s is None else float(end_s)
    out = []
    for ts, value in samples:
        rel = float(ts) - float(origin_ts)
        if start_value is not None and rel < start_value:
            continue
        if end_value is not None and rel > end_value:
            continue
        out.append((float(ts), float(value)))
    return out


def _remove_linear_trend(points):
    if len(points) < 2:
        return list(points)
    xs = [float(x) for x, _y in points]
    ys = [float(y) for _x, y in points]
    mean_x = sum(xs) / float(len(xs))
    mean_y = sum(ys) / float(len(ys))
    var_x = sum((x - mean_x) * (x - mean_x) for x in xs)
    if var_x <= 0.0:
        return [(x, y - mean_y) for x, y in points]
    cov_xy = sum((xs[idx] - mean_x) * (ys[idx] - mean_y) for idx in range(len(xs)))
    slope = cov_xy / var_x
    intercept = mean_y - (slope * mean_x)
    return [(x, y - ((slope * x) + intercept)) for x, y in points]


def _difference_uniform(points, sample_rate_hz):
    if len(points) < 2 or sample_rate_hz is None or sample_rate_hz <= 0.0:
        return []
    out = []
    prev_x, prev_y = points[0]
    for x_val, y_val in points[1:]:
        out.append((float(x_val), (float(y_val) - float(prev_y)) * float(sample_rate_hz)))
        prev_x, prev_y = x_val, y_val
    return out


def _analyze_signal(samples, *, origin_ts=None, analysis_start_s=None, analysis_end_s=None, remove_mean=True, detrend=False, use_delta=False):
    origin = float(origin_ts) if origin_ts is not None else (float(samples[0][0]) if samples else 0.0)
    sliced = _slice_samples_by_relative_time(samples, origin, analysis_start_s, analysis_end_s)
    resampled = _resample_uniform(sliced)
    clean = resampled["clean"]
    uniform = resampled["uniform"]
    sample_rate_hz = resampled["sample_rate_hz"]
    if len(clean) < 3:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": uniform,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    fft_size = _largest_power_of_two_leq(len(uniform))
    if fft_size < 8 or sample_rate_hz is None:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": uniform,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    uniform = uniform[-fft_size:]
    processed = [(float(ts) - origin, float(v)) for ts, v in uniform]
    if use_delta:
        processed = _difference_uniform(processed, sample_rate_hz)
    if detrend:
        processed = _remove_linear_trend(processed)
    if remove_mean and processed:
        mean_value = sum(float(v) for _ts, v in processed) / float(len(processed))
        processed = [(x_val, float(y_val) - mean_value) for x_val, y_val in processed]

    fft_size = _largest_power_of_two_leq(len(processed))
    if fft_size < 8 or sample_rate_hz is None:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": processed,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }
    processed = processed[-fft_size:]
    if fft_size <= 1:
        return {
            "selected": clean,
            "clean": clean,
            "uniform": uniform,
            "processed": processed,
            "sample_rate_hz": sample_rate_hz,
            "fft_size": 0,
            "span_s": resampled["span_s"],
            "spectrum": [],
            "peak_freq_hz": None,
            "peak_amplitude": None,
            "median_dt_s": resampled["median_dt_s"],
        }

    window = [0.5 - (0.5 * math.cos((2.0 * math.pi * idx) / float(fft_size - 1))) for idx in range(fft_size)]
    coherent_gain = sum(window) / float(fft_size)
    fft_input = [complex(float(processed[idx][1]) * window[idx], 0.0) for idx in range(fft_size)]
    spectrum_complex = _fft_complex(fft_input)

    spectrum = []
    half = fft_size // 2
    peak_freq_hz = None
    peak_amplitude = None
    for idx in range(half + 1):
        freq = (float(idx) * float(sample_rate_hz)) / float(fft_size)
        magnitude = abs(spectrum_complex[idx]) / float(fft_size)
        if coherent_gain > 0.0:
            magnitude /= coherent_gain
        if idx != 0 and idx != half:
            magnitude *= 2.0
        spectrum.append((freq, magnitude))
        if idx == 0:
            continue
        if peak_amplitude is None or magnitude > peak_amplitude:
            peak_amplitude = magnitude
            peak_freq_hz = freq

    return {
        "selected": clean,
        "clean": clean,
        "uniform": uniform,
        "processed": processed,
        "sample_rate_hz": sample_rate_hz,
        "fft_size": fft_size,
        "span_s": resampled["span_s"],
        "spectrum": spectrum,
        "peak_freq_hz": peak_freq_hz,
        "peak_amplitude": peak_amplitude,
        "median_dt_s": resampled["median_dt_s"],
    }


def estimate_frf(input_samples, output_samples, segment=256, overlap=0.5):
    """Welch H1, one-sided PSD and coherence on a shared uniform time grid."""
    from bisect import bisect_right
    x, y = _sanitize_samples(input_samples), _sanitize_samples(output_samples)
    if min(len(x), len(y)) < 3:
        raise ValueError("Both signals need captured samples")
    if segment < 16 or segment & (segment-1) or not 0 <= overlap < 1:
        raise ValueError("Invalid FFT segment or overlap")
    dt = max(_median([b[0]-a[0] for a,b in zip(z,z[1:])]) for z in (x,y))
    start, stop = max(x[0][0],y[0][0]), min(x[-1][0],y[-1][0])
    count = min(65536, max(0,int(math.floor((stop-start)/dt))+1))
    stride = max(1,int(segment*(1-overlap)))
    if count < segment+stride:
        raise ValueError("Capture needs at least two overlapping FFT segments")
    def align(z):
        times = [p[0] for p in z]
        values = []
        for i in range(count):
            t = start+i*dt
            j = min(len(z)-2,max(0,bisect_right(times,t)-1))
            a,b = z[j],z[j+1]
            if b[0]-a[0] > 4*dt:
                values.append(None)
            else:
                values.append(a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0]))
        return values
    x,y = align(x),align(y)
    win = [0.5-0.5*math.cos(2*math.pi*i/(segment-1)) for i in range(segment)]
    bins = segment//2+1
    xx,yy,xy = [0.0]*bins,[0.0]*bins,[0j]*bins
    used = 0
    for offset in range(0,count-segment+1,stride):
        a,b = x[offset:offset+segment],y[offset:offset+segment]
        if None in a or None in b: continue
        ma,mb = sum(a)/segment,sum(b)/segment
        X = _fft_complex([(v-ma)*w for v,w in zip(a,win)])
        Y = _fft_complex([(v-mb)*w for v,w in zip(b,win)])
        for k in range(bins):
            xx[k]+=abs(X[k])**2
            yy[k]+=abs(Y[k])**2
            xy[k]+=X[k].conjugate()*Y[k]
        used+=1
    if used<2:
        raise ValueError("Not enough continuous overlapping segments")
    result = {key:[] for key in ("input PSD","output PSD","Magnitude","Phase","Coherence")}
    scale = dt/(used*sum(w*w for w in win))
    for k in range(1,bins):
        f = k/(segment*dt)
        factor = scale*(1 if k==bins-1 else 2)
        result["input PSD"].append((f,xx[k]*factor))
        result["output PSD"].append((f,yy[k]*factor))
        if xx[k]>max(xx)*1e-12 and yy[k]>max(yy)*1e-12:
            h=xy[k]/xx[k]
            if abs(h)>0:
                result["Magnitude"].append((f,20*math.log10(abs(h))))
                result["Phase"].append((f,math.degrees(math.atan2(h.imag,h.real))))
            result["Coherence"].append((f,min(1.0,abs(xy[k])**2/(xx[k]*yy[k]))))
    return result,used,1/dt


class FrfWindow(QtWidgets.QWidget):
    def __init__(self, samples, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Frequency response / Welch PSD")
        self.resize(850,700)
        self.samples=samples
        layout=QtWidgets.QVBoxLayout(self)
        row=QtWidgets.QHBoxLayout()
        self.input=QtWidgets.QComboBox()
        self.output=QtWidgets.QComboBox()
        for combo in (self.input,self.output): combo.addItems(list(samples))
        self.output.setCurrentIndex(1)
        self.segment=QtWidgets.QComboBox()
        self.segment.addItems(["Auto","64","128","256","512","1024","2048"])
        self.segment.setCurrentText("Auto")
        for label,widget in (("Input",self.input),("Output",self.output),("FFT N",self.segment)):
            row.addWidget(QtWidgets.QLabel(label))
            row.addWidget(widget)
        run=QtWidgets.QPushButton("Analyze")
        run.clicked.connect(self.analyze)
        row.addWidget(run)
        layout.addLayout(row)
        self.note=QtWidgets.QLabel("H1 · Hann window · 50% overlap · captured PV timestamps, not RT sampling")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        tabs=QtWidgets.QTabWidget()
        self.plots={}
        for label,unit in (("Magnitude","Gain [dB]"),("Phase","Phase [deg]"),("Coherence","Coherence"),("PSD","Power / Hz")):
            plot=SeriesPlotWidget()
            plot.set_axes(title=label,x_label="Frequency [Hz]",y_label=unit,empty_text="Select input/output and analyze.")
            plot.set_x_floor(0.0)
            self.plots[label]=plot
            tabs.addTab(plot,label)
        layout.addWidget(tabs)
    def set_samples(self, samples):
        old_input,old_output=self.input.currentText(),self.output.currentText()
        self.samples=samples
        for combo,old,index in ((self.input,old_input,0),(self.output,old_output,1)):
            combo.clear()
            combo.addItems(list(samples))
            combo.setCurrentIndex(combo.findText(old) if old in samples else min(index,len(samples)-1))
        for plot in self.plots.values(): plot.set_series({})
        self.note.setText("Select input and output, then Analyze. Captured PV timestamps, not RT sampling.")

    def analyze(self):
        a,b=self.input.currentText(),self.output.currentText()
        try:
            if a==b: raise ValueError("Select distinct input and output signals")
            choice=self.segment.currentText()
            if choice=="Auto":
                segment=min(2048,_largest_power_of_two_leq(min(len(self.samples[a]),len(self.samples[b]))//4))
                while segment>=16:
                    try:
                        result,n,fs=estimate_frf(self.samples[a],self.samples[b],segment)
                        break
                    except ValueError:
                        segment//=2
                else:
                    raise ValueError("Not enough overlapping samples for frequency response")
            else:
                segment=int(choice)
                result,n,fs=estimate_frf(self.samples[a],self.samples[b],segment)
        except (ValueError,KeyError,ZeroDivisionError) as exc:
            self.note.setText(str(exc))
            for plot in self.plots.values(): plot.set_series({})
            return
        for key in ("Magnitude","Phase","Coherence"):
            self.plots[key].set_series({key:result[key]})
        self.plots["PSD"].set_series({a:result["input PSD"],b:result["output PSD"]})
        self.note.setText(f"H1 · Hann · 50% overlap · FFT {segment} · {n} segments · Fs {fs:.3g} Hz. Verify excitation and timestamp quality.")


class SpectrumWindow(QtWidgets.QMainWindow):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("PV DAQ - Frequency Domain")
        self.resize(860, 640)
        root = QtWidgets.QWidget(self)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, root)
        layout.addWidget(splitter, 1)

        self.time_plot_widget = SeriesPlotWidget(self)
        self.time_plot_widget.set_axes(
            title="Time Domain",
            x_label="Time [s]",
            y_label="Value",
            empty_text="Capture data in the main window to view the time-domain signal.",
        )
        self.time_plot_widget.set_x_floor(0.0)
        self.time_plot_widget.setMinimumHeight(140)
        splitter.addWidget(self.time_plot_widget)

        self.plot_widget = SeriesPlotWidget(self)
        self.plot_widget.set_axes(
            title="Frequency Domain",
            x_label="Frequency [Hz]",
            y_label="Amplitude",
            empty_text="Capture data in the main window to view the FFT.",
        )
        self.plot_widget.set_x_floor(0.0)
        self.plot_widget.set_y_floor(0.0)
        self.plot_widget.setMinimumHeight(280)
        splitter.addWidget(self.plot_widget)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 7)

        self.setCentralWidget(root)
        toolbar = self.addToolBar("View")
        toolbar.setMovable(False)
        reset_time_action = toolbar.addAction("Reset Time Zoom")
        reset_fft_action = toolbar.addAction("Reset FFT Zoom")
        reset_time_action.triggered.connect(self.time_plot_widget.reset_zoom)
        reset_fft_action.triggered.connect(self.plot_widget.reset_zoom)
        self.statusBar().showMessage("Idle")

    def set_spectrum(self, series, summary_text=""):
        self.plot_widget.set_series(series)
        self.statusBar().showMessage(str(summary_text or "Idle"))

    def set_plots(self, time_series, spectrum_series, summary_text=""):
        self.time_plot_widget.set_series(time_series)
        self.plot_widget.set_series(spectrum_series)
        self.statusBar().showMessage(str(summary_text or "Idle"))


class PvMonitorSource(QtCore.QObject):
    sample_ready = Signal(str, float, float)
    error_text = Signal(str)
    backend_text = Signal(str)

    def __init__(self, timeout=2.0, fallback_poll_ms=20, parent=None):
        super().__init__(parent)
        self.client = EpicsClient(timeout=timeout)
        self._poll_timer = QtCore.QTimer(self)
        self._poll_timer.timeout.connect(self._poll_once)
        self._poll_pvs = []
        self._monitor_pvs = []
        self._pyepics_module = self.client._epics if getattr(self.client, "backend", "") == "pyepics" else None

        self._fallback_poll_ms = 20
        self.set_fallback_poll_ms(fallback_poll_ms)
        self.backend_text.emit(self.backend_name())

    def backend_name(self):
        backend = str(getattr(self.client, "backend", "") or "unknown")
        if self._pyepics_module is not None:
            return f"{backend} (monitor)"
        return f"{backend} (poll)"

    def set_timeout(self, timeout_s):
        self.client.timeout = float(timeout_s)

    def set_fallback_poll_ms(self, interval_ms):
        self._fallback_poll_ms = max(5, int(round(float(interval_ms))))
        if self._poll_timer.isActive():
            self._poll_timer.setInterval(self._fallback_poll_ms)

    def start(self, pv_names):
        self.stop()
        pvs = []
        seen = set()
        for pv in pv_names or []:
            name = str(pv or "").strip()
            if name and name not in seen:
                seen.add(name)
                pvs.append(name)
        if not pvs:
            return

        if self._pyepics_module is not None:
            self._start_pyepics_monitors(pvs)
            self.backend_text.emit(self.backend_name())
            return

        self._poll_pvs = list(pvs)
        self._poll_timer.start(self._fallback_poll_ms)
        self.backend_text.emit(self.backend_name())

    def stop(self):
        self._poll_timer.stop()
        self._poll_pvs = []
        for pv_obj in self._monitor_pvs:
            try:
                pv_obj.clear_callbacks()
            except Exception:
                pass
            try:
                pv_obj.auto_monitor = False
            except Exception:
                pass
            try:
                pv_obj.disconnect()
            except Exception:
                pass
        self._monitor_pvs = []

    def _start_pyepics_monitors(self, pv_names):
        self._monitor_pvs = []
        for name in pv_names:
            try:
                pv_obj = self._pyepics_module.PV(name, auto_monitor=True)
                if hasattr(pv_obj, "wait_for_connection") and not pv_obj.wait_for_connection(timeout=float(self.client.timeout)):
                    self.error_text.emit(f"PV monitor failed to connect: {name}")
                    continue
                pv_obj.add_callback(callback=self._make_callback(name))
                self._monitor_pvs.append(pv_obj)
            except Exception as ex:
                self.error_text.emit(f"PV monitor setup failed for {name}: {ex}")

    def _make_callback(self, pv_name):
        def _callback(pvname=None, value=None, timestamp=None, **_kwargs):
            num = _coerce_float(value)
            if num is None:
                return
            ts = float(timestamp) if timestamp not in (None, "") else time.time()
            if not math.isfinite(ts):
                ts = time.time()
            self.sample_ready.emit(str(pv_name or pvname or ""), ts, num)

        return _callback

    def _poll_once(self):
        for name in list(self._poll_pvs):
            try:
                raw = self.client.get(name, as_string=True)
                num = _coerce_float(raw)
                if num is None:
                    continue
                self.sample_ready.emit(name, time.time(), num)
            except Exception as ex:
                self.error_text.emit(f"Polling failed for {name}: {ex}")


class DaqWindow(QtWidgets.QMainWindow):
    def __init__(self, default_prefix="", initial_pvs=None, timeout=2.0):
        super().__init__()
        self.setWindowTitle("PV DAQ - Time Domain")
        self.resize(980, 680)

        self._capture_active = False
        self._trigger = None
        self._trigger_deadline = None
        self._capture_start_ts = None
        self._capture_goal = 8192
        self._samples_by_pv = {}
        self._analysis_by_pv = {}
        self._default_prefix = str(default_prefix or "").strip()
        self._pending_refresh = False
        self._spectrum_window_shown_once = False
        self._analysis_start_s = 0.0
        self._analysis_end_s = 0.0
        self._analysis_range_auto = True
        self._last_session_dir = Path.home()
        self._last_image_dir = Path.home()

        self.source = PvMonitorSource(timeout=timeout, fallback_poll_ms=20, parent=self)
        self.source.sample_ready.connect(self._on_sample_ready)
        self.source.error_text.connect(self._log)
        self.source.backend_text.connect(self._set_backend_text)

        self.spectrum_window = SpectrumWindow()
        self._build_ui()

        self._refresh_timer = QtCore.QTimer(self)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_views)

        self.prefix_edit.setText(self._default_prefix)
        for pv in initial_pvs or []:
            self._add_pv(str(pv or "").strip(), log=False)
        self._refresh_views()

    def _build_ui(self):
        root = QtWidgets.QWidget(self)
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        top_row = QtWidgets.QHBoxLayout()
        self.prefix_edit = QtWidgets.QLineEdit()
        self.prefix_edit.setPlaceholderText("Optional IOC prefix for short PV names")
        self.timeout_spin = CompactDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setDecimals(2)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setValue(float(self.source.client.timeout))
        self.sample_count_spin = QtWidgets.QSpinBox()
        self.sample_count_spin.setRange(8, 131072)
        self.sample_count_spin.setSingleStep(128)
        self.sample_count_spin.setValue(self._capture_goal)
        self.poll_interval_spin = QtWidgets.QSpinBox()
        self.poll_interval_spin.setRange(5, 2000)
        self.poll_interval_spin.setSingleStep(5)
        self.poll_interval_spin.setSuffix(" ms")
        self.poll_interval_spin.setValue(20)
        self.backend_label = QtWidgets.QLabel(self.source.backend_name())
        self.backend_label.setStyleSheet("color: #475569;")
        self.backend_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.open_app_combo = QtWidgets.QComboBox()
        self.open_app_combo.setMinimumWidth(170)
        self.open_app_combo.addItem(APP_LAUNCH_PLACEHOLDER, "")
        self.open_app_combo.addItem(APP_LAUNCH_DAQ, "daq")
        self.open_app_combo.addItem(APP_LAUNCH_STREAM, "stream")
        self.open_app_combo.addItem(APP_LAUNCH_AXIS, "axis")
        self.open_app_combo.addItem(APP_LAUNCH_CONTROLLER, "controller")
        self.open_app_combo.addItem(APP_LAUNCH_MOTION, "motion")
        self.open_app_combo.addItem(APP_LAUNCH_ISO230, "iso230")
        self.open_app_combo.addItem(APP_LAUNCH_RTLOG, "rtlog")
        self.open_app_combo.addItem(APP_LAUNCH_CAQTDM_MAIN, "caqtdm_main")

        top_row.addWidget(QtWidgets.QLabel("IOC Prefix"))
        top_row.addWidget(self.prefix_edit, 1)
        top_row.addSpacing(8)
        top_row.addWidget(QtWidgets.QLabel("Timeout [s]"))
        top_row.addWidget(self.timeout_spin)
        top_row.addSpacing(8)
        self.continuous = QtWidgets.QCheckBox("Continuous")
        self.continuous.setChecked(True)
        self.continuous.setToolTip("Keep latest samples per PV until Stop; bounded by buffer size.")
        top_row.addWidget(self.continuous)
        top_row.addWidget(QtWidgets.QLabel("Buffer / PV"))
        top_row.addWidget(self.sample_count_spin)
        top_row.addSpacing(8)
        top_row.addWidget(QtWidgets.QLabel("Fallback Poll"))
        top_row.addWidget(self.poll_interval_spin)
        top_row.addSpacing(12)
        top_row.addWidget(QtWidgets.QLabel("Backend"))
        top_row.addWidget(self.backend_label)
        top_row.addSpacing(12)
        top_row.addWidget(QtWidgets.QLabel("Launch"))
        top_row.addWidget(self.open_app_combo)
        setup = QtWidgets.QGroupBox('Connection / acquisition settings')
        setup.setCheckable(True)
        setup.setChecked(False)
        setup_body = QtWidgets.QWidget()
        setup_body.setLayout(top_row)
        setup_layout = QtWidgets.QVBoxLayout(setup)
        setup_layout.addWidget(setup_body)
        setup_body.setVisible(False)
        setup.toggled.connect(setup_body.setVisible)
        layout.addWidget(setup)

        proc_row = QtWidgets.QHBoxLayout()
        self.remove_mean_chk = QtWidgets.QCheckBox("Remove Mean")
        self.remove_mean_chk.setChecked(True)
        self.detrend_chk = QtWidgets.QCheckBox("Detrend")
        self.use_delta_chk = QtWidgets.QCheckBox("Use Delta / Rate")
        self.range_start_spin = CompactDoubleSpinBox()
        self.range_start_spin.setRange(0.0, 1e9)
        self.range_start_spin.setDecimals(4)
        self.range_start_spin.setSingleStep(0.1)
        self.range_end_spin = CompactDoubleSpinBox()
        self.range_end_spin.setRange(0.0, 1e9)
        self.range_end_spin.setDecimals(4)
        self.range_end_spin.setSingleStep(0.1)
        reset_range_btn = QtWidgets.QPushButton("Full Capture Range")
        reset_time_zoom_btn = QtWidgets.QPushButton("Reset Time Zoom")
        reset_range_btn.setAutoDefault(False)
        reset_range_btn.setDefault(False)
        reset_time_zoom_btn.setAutoDefault(False)
        reset_time_zoom_btn.setDefault(False)

        proc_row.addWidget(QtWidgets.QLabel("FFT Prep"))
        proc_row.addWidget(self.remove_mean_chk)
        proc_row.addWidget(self.detrend_chk)
        proc_row.addWidget(self.use_delta_chk)
        proc_row.addSpacing(16)
        proc_row.addWidget(QtWidgets.QLabel("Start [s]"))
        proc_row.addWidget(self.range_start_spin)
        proc_row.addWidget(QtWidgets.QLabel("End [s]"))
        proc_row.addWidget(self.range_end_spin)
        proc_row.addWidget(reset_range_btn)
        proc_row.addWidget(reset_time_zoom_btn)
        proc_row.addStretch(1)
        analysis_group = QtWidgets.QGroupBox('Analysis settings')
        analysis_group.setCheckable(True)
        analysis_group.setChecked(False)
        analysis_body = QtWidgets.QWidget()
        analysis_body.setLayout(proc_row)
        analysis_layout = QtWidgets.QVBoxLayout(analysis_group)
        analysis_layout.addWidget(analysis_body)
        analysis_body.setVisible(False)
        analysis_group.toggled.connect(analysis_body.setVisible)
        layout.addWidget(analysis_group)

        file_row = QtWidgets.QHBoxLayout()
        self.log_toggle_btn = QtWidgets.QPushButton("Show Log")
        save_session_btn = QtWidgets.QPushButton("Save Session")
        load_session_btn = QtWidgets.QPushButton("Load Session")
        save_time_plot_btn = QtWidgets.QPushButton("Save Time Plot")
        save_fft_plot_btn = QtWidgets.QPushButton("Save FFT Plot")
        for btn in (self.log_toggle_btn, save_session_btn, load_session_btn, save_time_plot_btn, save_fft_plot_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        file_row.addWidget(self.log_toggle_btn)
        file_row.addSpacing(12)
        file_row.addWidget(save_session_btn)
        file_row.addWidget(load_session_btn)
        file_row.addSpacing(12)
        file_row.addWidget(save_time_plot_btn)
        file_row.addWidget(save_fft_plot_btn)
        file_row.addStretch(1)
        files = QtWidgets.QGroupBox("Session / export")
        files.setCheckable(True)
        files.setChecked(False)
        files_body = QtWidgets.QWidget()
        files_body.setLayout(file_row)
        QtWidgets.QVBoxLayout(files).addWidget(files_body)
        files_body.hide()
        files.toggled.connect(files_body.setVisible)
        layout.addWidget(files)

        pv_row = QtWidgets.QHBoxLayout()
        self.pv_edit = QtWidgets.QLineEdit()
        self.pv_edit.setPlaceholderText("PV name or suffix")
        add_btn = QtWidgets.QPushButton("Add PV")
        remove_btn = QtWidgets.QPushButton("Remove Selected")
        clear_pvs_btn = QtWidgets.QPushButton("Clear PVs")
        start_btn = QtWidgets.QPushButton("Start Capture")
        stop_btn = QtWidgets.QPushButton("Stop")
        clear_samples_btn = QtWidgets.QPushButton("Clear Samples")
        show_freq_btn = QtWidgets.QPushButton("FFT")
        for btn in (add_btn, remove_btn, clear_pvs_btn, start_btn, stop_btn, clear_samples_btn, show_freq_btn):
            btn.setAutoDefault(False)
            btn.setDefault(False)
        self.start_btn = start_btn
        self.stop_btn = stop_btn

        pv_row.addWidget(QtWidgets.QLabel("PV"))
        pv_row.addWidget(self.pv_edit, 1)
        pv_row.addWidget(add_btn)
        self.preset_btn=QtWidgets.QToolButton()
        self.preset_btn.setText("Presets")
        self.preset_btn.setPopupMode(QtWidgets.QToolButton.InstantPopup)
        preset_menu=QtWidgets.QMenu(self.preset_btn)
        for label,key in (("Axis positioning","position"),("Controller tuning","controller"),("RT thread timing","thread"),("EtherCAT domain","domain"),("RT logger","logger")):
            action=preset_menu.addAction(label)
            action.triggered.connect(lambda checked=False,k=key:self._add_preset(k))
        self.preset_btn.setMenu(preset_menu)
        pv_row.addWidget(self.preset_btn)

        pv_row.addSpacing(12)
        pv_row.addWidget(start_btn)
        pv_row.addWidget(stop_btn)
        pv_row.addWidget(clear_samples_btn)
        show_freq_btn.hide()
        frf_btn = QtWidgets.QPushButton("FRF / PSD")
        frf_btn.clicked.connect(self._show_frf)
        frf_btn.hide()
        layout.addLayout(pv_row)
        trigger_row = QtWidgets.QHBoxLayout()
        self.trigger_enabled = QtWidgets.QCheckBox('Triggered capture')
        self.trigger_pv = QtWidgets.QLineEdit()
        self.trigger_pv.setPlaceholderText('Trigger PV (e.g. axis-ErrId)')
        self.trigger_edge = QtWidgets.QComboBox()
        self.trigger_edge.addItems(['Rising ≥','Falling ≤','Absolute ≥'])
        self.trigger_threshold = CompactDoubleSpinBox()
        self.trigger_threshold.setRange(-1e12,1e12)
        self.trigger_threshold.setDecimals(6)
        self.trigger_threshold.setValue(1)
        self.trigger_pre = CompactDoubleSpinBox()
        self.trigger_post = CompactDoubleSpinBox()
        for spin,value in ((self.trigger_pre,2),(self.trigger_post,3)):
            spin.setRange(0.1,120)
            spin.setValue(value)
            spin.setSuffix(' s')
        trigger_row.addWidget(self.trigger_enabled)
        trigger_row.addWidget(self.trigger_pv,1)
        trigger_row.addWidget(self.trigger_edge)
        trigger_row.addWidget(self.trigger_threshold)
        trigger_row.addWidget(QtWidgets.QLabel('Pre'))
        trigger_row.addWidget(self.trigger_pre)
        trigger_row.addWidget(QtWidgets.QLabel('Post'))
        trigger_row.addWidget(self.trigger_post)
        trigger_group = QtWidgets.QGroupBox("Trigger settings")
        trigger_group.setCheckable(True)
        trigger_group.setChecked(False)
        trigger_body = QtWidgets.QWidget()
        trigger_body.setLayout(trigger_row)
        QtWidgets.QVBoxLayout(trigger_group).addWidget(trigger_body)
        trigger_body.hide()
        trigger_group.toggled.connect(trigger_body.setVisible)
        layout.addWidget(trigger_group)
        self.trigger_controls=(self.trigger_enabled,self.trigger_pv,self.trigger_edge,self.trigger_threshold,self.trigger_pre,self.trigger_post)
        self.capture_edit_buttons=(add_btn,remove_btn,clear_pvs_btn,clear_samples_btn,load_session_btn)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical, self)
        layout.addWidget(splitter, 1)

        upper = QtWidgets.QWidget()
        upper_layout = QtWidgets.QHBoxLayout(upper)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(8)

        left = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)

        left_layout.addWidget(QtWidgets.QLabel("Selected PVs"))
        self.pv_list = QtWidgets.QListWidget()
        self.pv_list.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.pv_list.setAlternatingRowColors(True)
        self.pv_list.itemChanged.connect(lambda *_: self._visibility_changed())
        self.pv_list.currentItemChanged.connect(lambda *_: self._update_cursor_readout())
        left_layout.addWidget(self.pv_list, 1)
        signal_actions = QtWidgets.QHBoxLayout()
        remove_btn.setText("Remove")
        clear_pvs_btn.setText("Clear")
        signal_actions.addWidget(remove_btn)
        signal_actions.addWidget(clear_pvs_btn)
        left_layout.addLayout(signal_actions)
        self.normalize_plot=QtWidgets.QCheckBox("Normalize each signal")
        self.normalize_plot.setToolTip("Scale each visible time-domain signal independently to 0–1. Raw samples and FFT are unchanged.")
        self.normalize_plot.toggled.connect(lambda *_: self._visibility_changed())
        left_layout.addWidget(self.normalize_plot)
        self.cursor_readout=QtWidgets.QLabel("Drag time markers to measure")
        self.cursor_readout.setWordWrap(True)
        self.cursor_readout.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        left_layout.addWidget(self.cursor_readout)
        self.motion_metrics=QtWidgets.QLabel("Select a signal to inspect noise and sampling")
        self.motion_metrics.setWordWrap(True)
        self.motion_metrics.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        left_layout.addWidget(self.motion_metrics)

        note = QtWidgets.QLabel(
            "FFT uses the captured timestamps to estimate the effective sample rate per PV. "
            "Signals are linearly resampled onto a uniform grid before the FFT is calculated. "
            "Wheel zooms horizontally, Shift+wheel zooms vertically, Ctrl+wheel zooms both axes, "
            "right-drag pans, and right double-click resets zoom. "
            "Drag the start/end markers in the time plot, or left double-click there to reset to the full capture."
        )
        note.setToolTip(note.text())
        note.setText('Wheel: zoom · right drag: pan · drag range markers to select FFT interval.')
        note.setWordWrap(True)
        note.setStyleSheet("color: #516079;")
        left_layout.addWidget(note)
        left.setMaximumWidth(260)
        upper_layout.addWidget(left, 0)

        self.time_plot = SeriesPlotWidget()
        self.time_plot.set_axes(
            title="Time Domain",
            x_label="Time [s]",
            y_label="Value",
            empty_text="Add one or more PVs and start a capture.",
        )
        self.time_plot.set_x_floor(0.0)
        self.time_plot.set_selection_enabled(True)
        self.analysis_tabs = QtWidgets.QTabWidget()
        self.analysis_tabs.addTab(self.time_plot, "Time domain")
        self.analysis_tabs.addTab(self.spectrum_window.plot_widget, "FFT")
        self.frf_panel=FrfWindow({},self)
        self.analysis_tabs.addTab(self.frf_panel,"Bode / PSD")
        self.analysis_tabs.currentChanged.connect(self._analysis_tab_changed)
        upper_layout.addWidget(self.analysis_tabs, 1)
        splitter.addWidget(upper)

        lower = QtWidgets.QWidget()
        lower_layout = QtWidgets.QVBoxLayout(lower)
        lower_layout.setContentsMargins(0, 0, 0, 0)
        lower_layout.setSpacing(6)

        self.capture_status = QtWidgets.QLabel("Idle")
        self.capture_status.setStyleSheet("font-weight: 600; color: #0f172a;")
        lower_layout.addWidget(self.capture_status)

        self.stats_table = QtWidgets.QTableWidget(0, 8)
        self.stats_table.setHorizontalHeaderLabels(
            ["PV", "Samples", "Span [s]", "Fs [Hz]", "FFT N", "Peak [Hz]", "Peak Amp", "Last"]
        )
        self.stats_table.setAlternatingRowColors(True)
        self.stats_table.verticalHeader().setVisible(False)
        self.stats_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.stats_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        header = self.stats_table.horizontalHeader()
        header.setSectionResizeMode(0, QtWidgets.QHeaderView.Stretch)
        for column in range(1, 8):
            header.setSectionResizeMode(column, QtWidgets.QHeaderView.ResizeToContents)
        lower_layout.addWidget(self.stats_table, 1)

        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setPlaceholderText("Log")
        self.log.setMaximumBlockCount(500)
        self.log.setMinimumHeight(140)
        self.log.setVisible(False)
        lower_layout.addWidget(self.log)
        splitter.addWidget(lower)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

        self.timeout_spin.valueChanged.connect(self._on_timeout_changed)
        self.poll_interval_spin.valueChanged.connect(self._on_poll_interval_changed)
        self.sample_count_spin.valueChanged.connect(self._on_sample_goal_changed)
        self.open_app_combo.activated.connect(self._on_open_app_selected)
        self.remove_mean_chk.toggled.connect(self._on_analysis_settings_changed)
        self.detrend_chk.toggled.connect(self._on_analysis_settings_changed)
        self.use_delta_chk.toggled.connect(self._on_analysis_settings_changed)
        self.range_start_spin.valueChanged.connect(self._on_analysis_range_spin_changed)
        self.range_end_spin.valueChanged.connect(self._on_analysis_range_spin_changed)
        self.time_plot.range_changed.connect(self._on_plot_range_changed)
        self.pv_edit.returnPressed.connect(self._add_pv_from_editor)
        add_btn.clicked.connect(self._add_pv_from_editor)
        remove_btn.clicked.connect(self._remove_selected_pvs)
        clear_pvs_btn.clicked.connect(self._clear_pvs)
        start_btn.clicked.connect(self.start_capture)
        stop_btn.clicked.connect(self.stop_capture)
        clear_samples_btn.clicked.connect(self.clear_samples)
        show_freq_btn.clicked.connect(self._show_spectrum_window)
        reset_range_btn.clicked.connect(self._reset_analysis_range_to_full_capture)
        reset_time_zoom_btn.clicked.connect(self.time_plot.reset_zoom)
        save_session_btn.clicked.connect(self.save_session)
        load_session_btn.clicked.connect(self.load_session)
        save_time_plot_btn.clicked.connect(lambda: self._save_plot_image(self.time_plot, "time"))
        save_fft_plot_btn.clicked.connect(lambda: self._save_plot_image(self.spectrum_window.plot_widget, "fft"))
        self.log_toggle_btn.clicked.connect(self._toggle_log_panel)

    def showEvent(self, event):
        super().showEvent(event)
        self._refresh_views()

    def closeEvent(self, event):
        self.stop_capture()
        self.spectrum_window.close()
        super().closeEvent(event)

    def _set_backend_text(self, text):
        self.backend_label.setText(str(text or ""))

    def _on_timeout_changed(self, value):
        self.source.set_timeout(value)

    def _reset_open_app_combo(self):
        reset_combo(self.open_app_combo)

    def _on_open_app_selected(self, index):
        action = str(self.open_app_combo.itemData(index) or "")
        try:
            if action == "daq":
                self._open_daq_window()
            elif action == "stream":
                self._open_stream_window()
            elif action == "axis":
                self._open_script_window("start_axis.sh", "axis cfg")
            elif action == "controller":
                self._open_script_window("start_cntrl.sh", "cntrl cfg")
            elif action == "motion":
                self._open_script_window("start_mtn.sh", "motion")
            elif action == "iso230":
                self._open_script_window("start_iso230.sh", "iso230")
            elif action == "rtlog":
                self._open_script_window("start_rtlog.sh", "rt logger")
            elif action == "caqtdm_main":
                self._open_caqtdm_main_panel()
        finally:
            self._reset_open_app_combo()

    def _current_prefix(self):
        return self.prefix_edit.text().strip() or self._default_prefix or "IOC:ECMC"

    def _open_script_window(self, script_name, label, extra_args=None):
        script = Path(__file__).with_name(script_name)
        if not script.exists():
            self._log(f"Launcher not found: {script.name}")
            return False
        cmd = ["bash", str(script), str(self._current_prefix())]
        for arg in extra_args or []:
            text = str(arg or "").strip()
            if text:
                cmd.append(text)
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

    def _open_stream_window(self):
        return self._open_script_window("start.sh", "stream")

    def _open_daq_window(self):
        return self._open_script_window("start_daq.sh", "daq", extra_args=self._selected_pvs())

    def _open_caqtdm_main_panel(self):
        ioc_prefix = self._current_prefix()
        macro = f"IOC={ioc_prefix}"
        try:
            launch_caqtdm('ecmcMain.ui', macro)
            self._log(f"Started caQtDM main panel ({macro})")
        except Exception as ex:
            self._log(f"Failed to start caQtDM main panel: {ex}")

    def _toggle_log_panel(self):
        visible = not self.log.isVisible()
        self.log.setVisible(visible)
        self.log_toggle_btn.setText("Hide Log" if visible else "Show Log")

    def _on_poll_interval_changed(self, value):
        self.source.set_fallback_poll_ms(value)

    def _on_sample_goal_changed(self, value):
        self._capture_goal = int(value)

    def _on_analysis_settings_changed(self, _checked=False):
        self._refresh_views()

    def _set_analysis_range(self, start_s, end_s, *, from_plot=False):
        start = max(0.0, float(start_s or 0.0))
        end = max(start, float(end_s or start))
        self._analysis_start_s = start
        self._analysis_end_s = end
        self._analysis_range_auto = False
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setValue(start)
        self.range_end_spin.setValue(end)
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        if not from_plot:
            self.time_plot.set_selection_range(start, end)
        self._refresh_views()

    def _analysis_controls_span(self):
        max_span = 0.0
        for samples in self._samples_by_pv.values():
            clean = _sanitize_samples(samples)
            if len(clean) >= 2:
                origin = self._capture_start_ts if self._capture_start_ts is not None else clean[0][0]
                max_span = max(max_span, float(clean[-1][0]) - float(origin))
        return max_span

    def _sync_analysis_range_controls(self, *, keep_manual=True):
        max_span = max(0.0, self._analysis_controls_span())
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setRange(0.0, max(0.0, max_span))
        self.range_end_spin.setRange(0.0, max(0.0, max_span))
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        if max_span <= 0.0:
            self._analysis_start_s = 0.0
            self._analysis_end_s = 0.0
            self.time_plot.set_selection_range(0.0, 0.0)
            self.range_start_spin.blockSignals(True)
            self.range_end_spin.blockSignals(True)
            self.range_start_spin.setValue(0.0)
            self.range_end_spin.setValue(0.0)
            self.range_start_spin.blockSignals(False)
            self.range_end_spin.blockSignals(False)
            self._analysis_range_auto = True
            return
        if self._analysis_range_auto or not keep_manual:
            self._analysis_start_s = 0.0
            self._analysis_end_s = max_span
        else:
            self._analysis_start_s = min(self._analysis_start_s, max_span)
            self._analysis_end_s = min(max(self._analysis_end_s, self._analysis_start_s), max_span)
        self.range_start_spin.blockSignals(True)
        self.range_end_spin.blockSignals(True)
        self.range_start_spin.setValue(self._analysis_start_s)
        self.range_end_spin.setValue(self._analysis_end_s)
        self.range_start_spin.blockSignals(False)
        self.range_end_spin.blockSignals(False)
        self.time_plot.set_selection_range(self._analysis_start_s, self._analysis_end_s)

    def _reset_analysis_range_to_full_capture(self):
        self._analysis_range_auto = True
        self._sync_analysis_range_controls(keep_manual=False)
        self._refresh_views()

    def _on_analysis_range_spin_changed(self, _value):
        self._set_analysis_range(self.range_start_spin.value(), self.range_end_spin.value())

    def _on_plot_range_changed(self, start_s, end_s):
        self._set_analysis_range(start_s, end_s, from_plot=True)

    def _resolved_pv_name(self, text):
        raw = str(text or "").strip()
        if not raw:
            return ""
        prefix = self.prefix_edit.text().strip()
        if prefix and ":" not in raw:
            return _join_prefix_pv(prefix, raw)
        return raw

    def _visibility_changed(self):
        self._pending_refresh = True
        if not self._capture_active:
            self._refresh_views()

    def _visible_pvs(self):
        return {self.pv_list.item(i).text().strip() for i in range(self.pv_list.count())
                if self.pv_list.item(i).checkState() == QtCore.Qt.Checked}

    def _add_signal_item(self, name):
        item = QtWidgets.QListWidgetItem(name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsUserCheckable)
        item.setCheckState(QtCore.Qt.Checked)
        self.pv_list.addItem(item)

    def _selected_pvs(self):
        return [self.pv_list.item(idx).text().strip() for idx in range(self.pv_list.count()) if self.pv_list.item(idx).text().strip()]

    def _session_payload(self):
        return {
            "version": 1,
            "trigger_timestamp": self._trigger.timestamp if self._trigger is not None and self._trigger.fired else None,
            "prefix": self.prefix_edit.text().strip(),
            "timeout_s": float(self.timeout_spin.value()),
            "fallback_poll_ms": int(self.poll_interval_spin.value()),
            "sample_goal": int(self.sample_count_spin.value()),
            "pvs": self._selected_pvs(),
            "capture_start_ts": None if self._capture_start_ts is None else float(self._capture_start_ts),
            "samples_by_pv": {
                str(name): [[float(ts), float(val)] for ts, val in (self._samples_by_pv.get(name) or [])]
                for name in self._selected_pvs()
            },
            "analysis": {
                "remove_mean": bool(self.remove_mean_chk.isChecked()),
                "detrend": bool(self.detrend_chk.isChecked()),
                "use_delta": bool(self.use_delta_chk.isChecked()),
                "start_s": float(self._analysis_start_s),
                "end_s": float(self._analysis_end_s),
                "range_auto": bool(self._analysis_range_auto),
            },
            "plots": {
                "time_view": self.time_plot.view_state(),
                "fft_view": self.spectrum_window.plot_widget.view_state(),
            },
        }

    def _apply_session_payload(self, payload):
        data = dict(payload or {})
        self.stop_capture()
        self._trigger=None
        self._trigger_deadline=None
        self._rolling_capture=False

        prefix = str(data.get("prefix", ""))
        timeout_s = float(data.get("timeout_s", self.timeout_spin.value()))
        fallback_poll_ms = int(data.get("fallback_poll_ms", self.poll_interval_spin.value()))
        sample_goal = int(data.get("sample_goal", self.sample_count_spin.value()))
        pvs = [str(pv or "").strip() for pv in (data.get("pvs") or []) if str(pv or "").strip()]
        samples_by_pv = dict(data.get("samples_by_pv") or {})
        analysis = dict(data.get("analysis") or {})
        plots = dict(data.get("plots") or {})

        self.prefix_edit.setText(prefix)
        self.timeout_spin.setValue(timeout_s)
        self.poll_interval_spin.setValue(fallback_poll_ms)
        self.sample_count_spin.setValue(sample_goal)

        self.remove_mean_chk.blockSignals(True)
        self.detrend_chk.blockSignals(True)
        self.use_delta_chk.blockSignals(True)
        self.remove_mean_chk.setChecked(bool(analysis.get("remove_mean", True)))
        self.detrend_chk.setChecked(bool(analysis.get("detrend", False)))
        self.use_delta_chk.setChecked(bool(analysis.get("use_delta", False)))
        self.remove_mean_chk.blockSignals(False)
        self.detrend_chk.blockSignals(False)
        self.use_delta_chk.blockSignals(False)

        self.pv_list.clear()
        self._samples_by_pv = {}
        self._analysis_by_pv = {}
        for pv in pvs:
            self._add_signal_item(pv)
            rows = []
            for pair in samples_by_pv.get(pv, []) or []:
                try:
                    ts, val = pair
                    rows.append((float(ts), float(val)))
                except Exception:
                    continue
            self._samples_by_pv[pv] = rows
            self._analysis_by_pv[pv] = {}

        capture_start = data.get("capture_start_ts")
        self._capture_start_ts = None if capture_start is None else float(capture_start)
        self._analysis_start_s = max(0.0, float(analysis.get("start_s", 0.0)))
        self._analysis_end_s = max(self._analysis_start_s, float(analysis.get("end_s", self._analysis_start_s)))
        self._analysis_range_auto = bool(analysis.get("range_auto", False))
        self.time_plot.set_view_state(plots.get("time_view"))
        self.spectrum_window.plot_widget.set_view_state(plots.get("fft_view"))
        self._sync_analysis_range_controls(keep_manual=not self._analysis_range_auto)
        self._refresh_views()

    def save_session(self):
        suggested = Path.home() / "fft_capture.json"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save FFT Session",
            str(suggested),
            "FFT Session (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            payload = self._session_payload()
            out_path = Path(path)
            out_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
            self._last_session_dir = out_path.parent
            self._log(f"Saved FFT session: {out_path}")
        except Exception as ex:
            self._log(f"Failed to save FFT session: {ex}")

    def load_session(self):
        path, _filter = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load FFT Session",
            str(self._last_session_dir),
            "FFT Session (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            in_path = Path(path)
            payload = json.loads(in_path.read_text())
            self._apply_session_payload(payload)
            self._last_session_dir = in_path.parent
            self._log(f"Loaded FFT session: {in_path}")
        except Exception as ex:
            self._log(f"Failed to load FFT session: {ex}")

    def _save_plot_image(self, widget, kind):
        suffix = "time" if str(kind) == "time" else "fft"
        suggested = Path.home() / f"fft_{suffix}.png"
        path, _filter = QtWidgets.QFileDialog.getSaveFileName(
            self,
            f"Save {suffix.upper()} Plot",
            str(suggested),
            "PNG Image (*.png);;JPEG Image (*.jpg *.jpeg);;BMP Image (*.bmp);;All Files (*)",
        )
        if not path:
            return
        try:
            out_path = Path(path)
            pixmap = widget.grab()
            if pixmap.isNull():
                raise RuntimeError("Could not capture plot image")
            if not pixmap.save(str(out_path)):
                raise RuntimeError("Qt image save failed")
            self._last_image_dir = out_path.parent
            self._log(f"Saved {suffix} plot image: {out_path}")
        except Exception as ex:
            self._log(f"Failed to save {suffix} plot image: {ex}")

    def _add_pv(self, pv_name, log=True):
        name = self._resolved_pv_name(pv_name)
        if not name:
            return
        existing = {self.pv_list.item(idx).text().strip() for idx in range(self.pv_list.count())}
        if name in existing:
            return
        self._add_signal_item(name)
        self._samples_by_pv.setdefault(name, [])
        self._analysis_by_pv.setdefault(name, {})
        if log:
            self._log(f"Added PV: {name}")
        self._refresh_views()

    def _add_preset(self, key):
        if self._capture_active:
            return
        from ecmc_diagnostic_presets import axis_diagnostic_pvs
        prefix=self.prefix_edit.text().strip().rstrip(":")
        if key in ("position","controller"):
            item=self.pv_list.currentItem()
            guess=""
            if item is not None:
                name=item.text()
                for suffix in ("PosAct","PosSet","PosErr","VelAct","TgtPosCmd","Busy","AtTarget","ErrId"):
                    if name.endswith("-"+suffix):
                        guess=name[:-(len(suffix)+1)]
                        break
            base,ok=QtWidgets.QInputDialog.getText(self,"Axis preset","Full axis PV base:",text=guess)
            if not ok or not base.strip():
                return
            names=axis_diagnostic_pvs(base.strip(),key)
        else:
            if not prefix:
                prefix,ok=QtWidgets.QInputDialog.getText(self,"IOC preset","IOC prefix:")
                if not ok or not prefix.strip():
                    return
                prefix=prefix.strip().rstrip(":")
                self.prefix_edit.setText(prefix)
            if key=="thread":
                names=[prefix+":MCU-Thd"+stem+edge for stem in ("Prd","Snd","Exe","Lat") for edge in ("Min","Max")]
            elif key=="logger":
                names=[prefix+":MCU-RTLog-"+suffix for suffix in ("Cnt","DropCnt","Level")]
            else:
                from ecmc_engineering_compare import domain_signals
                master,ok=QtWidgets.QInputDialog.getInt(self,"EtherCAT domain","Master index:",0,0,255)
                if not ok: return
                domain,ok=QtWidgets.QInputDialog.getInt(self,"EtherCAT domain","Domain index:",0,0,255)
                if not ok: return
                names=[name for index,name in domain_signals(prefix,master,domain+1) if index==domain]
        existing=set(self._selected_pvs())
        for name in names:
            if name not in existing:
                self._add_signal_item(name)
                self._samples_by_pv[name]=[]
                self._analysis_by_pv[name]={}
                existing.add(name)
        self._refresh_views()

    def _add_pv_from_editor(self):
        if self._capture_active:
            return
        self._add_pv(self.pv_edit.text().strip())
        self.pv_edit.clear()

    def _remove_selected_pvs(self):
        selected = self.pv_list.selectedItems()
        if not selected:
            return
        removed = []
        for item in selected:
            removed.append(item.text().strip())
            self.pv_list.takeItem(self.pv_list.row(item))
        for name in removed:
            self._samples_by_pv.pop(name, None)
            self._analysis_by_pv.pop(name, None)
        self._log(f"Removed {len(removed)} PV(s)")
        self._refresh_views()

    def _clear_pvs(self):
        if self._capture_active:
            self.stop_capture()
        self.pv_list.clear()
        self._samples_by_pv.clear()
        self._analysis_by_pv.clear()
        self._analysis_range_auto = True
        self._log("Cleared PV list")
        self._refresh_views()

    def clear_samples(self):
        self._capture_start_ts = None
        for name in self._selected_pvs():
            self._samples_by_pv[name] = []
            self._analysis_by_pv[name] = {}
        self._analysis_range_auto = True
        self._log("Cleared captured samples")
        self._refresh_views()

    def start_capture(self):
        pvs = self._selected_pvs()
        if not pvs:
            self._log("No PVs selected")
            QtWidgets.QMessageBox.information(self, "Capture", "Add at least one PV first.")
            return
        self._capture_goal = int(self.sample_count_spin.value())
        self._trigger = None
        self._trigger_deadline = None
        if self.trigger_enabled.isChecked():
            trigger_pv=self._resolved_pv_name(self.trigger_pv.text())
            if not trigger_pv or not self.trigger_pv.text().strip():
                self._log('Enter a trigger PV before arming')
                return
            self._trigger=TriggerCapture(pvs,trigger_pv,self.trigger_edge.currentIndex(),self.trigger_threshold.value(),self.trigger_pre.value(),self.trigger_post.value(),self._capture_goal)
        self._capture_start_ts = None
        self._capture_active = True
        self.frf_panel.set_samples({})
        self._rolling_capture = self.continuous.isChecked() and self._trigger is None
        self.analysis_tabs.setCurrentIndex(0)
        self._analysis_range_auto = True
        self._refresh_timer.start()
        for name in pvs:
            self._samples_by_pv[name] = deque(maxlen=self._capture_goal) if self._rolling_capture else []
            self._analysis_by_pv[name] = {}
        sources=list(pvs)
        if self._trigger is not None and self._trigger.trigger_pv not in sources:
            sources.append(self._trigger.trigger_pv)
        self.source.start(sources)
        self._update_capture_status()
        self._log(f"Capture started for {len(pvs)} PV(s), target {self._capture_goal} samples per PV")

    def stop_capture(self):
        if not self._capture_active and not self._refresh_timer.isActive():
            return
        self._capture_active = False
        self.source.stop()
        self._refresh_timer.stop()
        self._refresh_views()
        self._update_capture_status()
        if self.analysis_tabs.currentIndex()==2:
            self._update_frf_samples()

    def _analysis_tab_changed(self,index):
        self._pending_refresh=True
        if not self._capture_active:
            self._refresh_views()
        if index==2:
            self._update_frf_samples()

    def _update_frf_samples(self):
        if self._capture_active:
            self.frf_panel.set_samples({})
            self.frf_panel.note.setText("Stop capture before frequency response analysis.")
            return
        samples={p:_slice_samples_by_relative_time(self._samples_by_pv.get(p,[]),self._capture_start_ts,self._analysis_start_s,self._analysis_end_s) for p in self._selected_pvs()}
        self.frf_panel.set_samples({p:v for p,v in samples.items() if v})

    def _show_frf(self):
        self.analysis_tabs.setCurrentIndex(2)

    def _show_spectrum_window(self):
        self.analysis_tabs.setCurrentIndex(1)

    def _on_sample_ready(self, pv_name, timestamp_s, value):
        name = str(pv_name or "").strip()
        if not self._capture_active or not name:
            return
        if self._trigger is not None:
            fired=self._trigger.add(name,float(timestamp_s),float(value))
            if fired:
                self._trigger_deadline=time.monotonic()+self._trigger.post_s
                self._log('Trigger fired; collecting post-trigger samples')
            if self._trigger.fired:
                self._samples_by_pv={k:list(q) for k,q in self._trigger.buffers.items()}
                self._capture_start_ts=self._trigger.timestamp-self._trigger.pre_s
                self._pending_refresh=True
            return
        if name not in self._samples_by_pv:
            return
        samples = self._samples_by_pv.setdefault(name, [])
        if not self._rolling_capture and len(samples) >= self._capture_goal:
            return
        ts = float(timestamp_s)
        if not math.isfinite(ts):
            ts = time.time()
        val = finite_value(value)
        if val is None:
            return
        samples.append((ts, val))
        if self._capture_start_ts is None:
            self._capture_start_ts = ts
        self._pending_refresh = True
        if not self._rolling_capture and all(len(self._samples_by_pv.get(pv, [])) >= self._capture_goal for pv in self._selected_pvs()):
            self._log("Capture completed")
            self.stop_capture()

    def _refresh_views(self):
        if self._capture_active and self._trigger_deadline is not None and time.monotonic()>=self._trigger_deadline:
            self._trigger_deadline=None
            self.stop_capture()
            return
        if not self._pending_refresh and self._capture_active:
            self._update_capture_status()
            return
        self._pending_refresh = False
        if getattr(self,"_rolling_capture",False) and self._trigger is None:
            starts=[q[0][0] for q in self._samples_by_pv.values() if q]
            if starts and self._analysis_range_auto:
                self._capture_start_ts=min(starts)

        time_series = {}
        freq_series = {}
        summary_parts = []
        selected = self._selected_pvs()
        self._sync_analysis_range_controls()
        compute_fft = not self._capture_active or self.analysis_tabs.currentIndex()==1
        for pv in selected:
            samples = list(self._samples_by_pv.get(pv, []) or [])
            if samples and self._capture_start_ts is None:
                self._capture_start_ts = samples[0][0]
            if samples:
                origin = self._capture_start_ts if self._capture_start_ts is not None else samples[0][0]
                time_series[pv] = [(max(0.0, float(ts) - float(origin)), float(val)) for ts, val in samples]
            if not compute_fft:
                self._analysis_by_pv[pv] = {"span_s": max(0.0,samples[-1][0]-samples[0][0]) if len(samples)>1 else None}
                continue
            analysis = _analyze_signal(
                samples,
                origin_ts=self._capture_start_ts,
                analysis_start_s=self._analysis_start_s,
                analysis_end_s=self._analysis_end_s,
                remove_mean=self.remove_mean_chk.isChecked(),
                detrend=self.detrend_chk.isChecked(),
                use_delta=self.use_delta_chk.isChecked(),
            )
            self._analysis_by_pv[pv] = analysis
            if analysis.get("spectrum"):
                freq_series[pv] = analysis["spectrum"]
            if analysis.get("peak_freq_hz") is not None:
                summary_parts.append(
                    f"{pv}: peak {compact_float_text(analysis['peak_freq_hz'])} Hz @ {compact_float_text(analysis['peak_amplitude'])}"
                )

        visible = self._visible_pvs()
        shown={k:v for k,v in time_series.items() if k in visible}
        normalized=self.normalize_plot.isChecked()
        if normalized:
            for key,points in shown.items():
                if not points: continue
                lo=min(v for _,v in points)
                hi=max(v for _,v in points)
                shown[key]=[(t,(v-lo)/(hi-lo) if hi>lo else 0.5) for t,v in points]
        self.time_plot.set_axes(title="Time Domain",x_label="Time [s]",y_label="Normalized [0–1]" if normalized else "Value",empty_text="Add signals and start a capture.")
        self.time_plot.set_series(shown)
        if compute_fft:
            self.spectrum_window.set_plots({}, {k:v for k,v in freq_series.items() if k in visible}, " | ".join(summary_parts))
        else:
            self.spectrum_window.plot_widget.set_series({})
        self._reload_stats_table(selected)
        self._update_cursor_readout()
        self._update_capture_status()

    def _update_cursor_readout(self):
        if not hasattr(self,"cursor_readout") or not hasattr(self,"time_plot"):
            return
        from bisect import bisect_left
        a,b=self._analysis_start_s,self._analysis_end_s
        dt=b-a
        text=f"Δt: {dt:.6g} s"
        if dt>0:
            text+=f"  |  1/Δt: {1/dt:.6g} Hz"
        item=self.pv_list.currentItem()
        name=item.text() if item is not None else next(iter(self._selected_pvs()),None)
        origin=self._capture_start_ts
        if name and origin is not None:
            rows=_sanitize_samples(self._samples_by_pv.get(name,[]))
            stamps=[t for t,_ in rows]
            def at(relative):
                t=origin+relative
                i=bisect_left(stamps,t)
                if i<len(rows) and stamps[i]==t: return rows[i][1]
                if i==0 or i==len(rows): return None
                x0,y0=rows[i-1]
                x1,y1=rows[i]
                return y0+(y1-y0)*(t-x0)/(x1-x0)
            va,vb=at(a),at(b)
            text+="\n"+name+"\n"
            text+=f"ΔY: {vb-va:.6g}" if va is not None and vb is not None else "ΔY: — (outside samples)"
        self.cursor_readout.setText(text)
        self._update_motion_metrics(name,origin,a,b)
        self.cursor_readout.setToolTip("ΔY uses raw values of the selected PV, linearly interpolated at the time markers. 1/Δt is reciprocal interval, not a detected frequency.")

    def _update_motion_metrics(self,name,origin,a,b):
        if not hasattr(self,"motion_metrics"):
            return
        if self._capture_active:
            self.motion_metrics.setText("Stop capture for noise and sampling statistics.")
            return
        rows=_slice_samples_by_relative_time(self._samples_by_pv.get(name,[]),origin,a,b) if name and origin is not None else []
        m=motion_sample_metrics(rows)
        if not m:
            self.motion_metrics.setText("No samples in selected range")
            return
        lines=[f"Mean: {m['mean']:.6g}",f"Noise σ: {m['std']:.6g}  |  P-P: {m['pp']:.6g}",f"RMS: {m['rms']:.6g}"]
        if "rate" in m:
            lines.extend([f"Median Fs: {m['rate']:.4g} Hz  |  Events: {m['event_rate']:.4g}/s",
                f"Interval jitter: {m['jitter_ms']:.3g} ms",
                f"Long intervals: {m['gaps']}  |  Max: {m['max_gap_ms']:.3g} ms"])
            if m["gaps"] or m["jitter_ms"]*m["rate"]>100:
                lines.append("Irregular sampling — interpret FFT carefully")
        self.motion_metrics.setText("\n".join(lines))
        self.motion_metrics.setToolTip("Statistics use raw events within the time markers. Noise σ includes any motion or drift. Jitter is RMS deviation from median sample interval. Long intervals exceed twice the median; they do not prove lost samples.")

    def _reload_stats_table(self, pvs):
        self.stats_table.setRowCount(len(pvs))
        for row_idx, pv in enumerate(pvs):
            samples = list(self._samples_by_pv.get(pv, []) or [])
            analysis = dict(self._analysis_by_pv.get(pv, {}) or {})
            span_s = analysis.get("span_s")
            fs_hz = analysis.get("sample_rate_hz")
            fft_size = analysis.get("fft_size") or 0
            peak_hz = analysis.get("peak_freq_hz")
            peak_amp = analysis.get("peak_amplitude")
            last_value = samples[-1][1] if samples else None
            values = [
                pv,
                str(len(samples)),
                compact_float_text(span_s) if span_s is not None else "",
                compact_float_text(fs_hz) if fs_hz is not None else "",
                str(fft_size) if fft_size else "",
                compact_float_text(peak_hz) if peak_hz is not None else "",
                compact_float_text(peak_amp) if peak_amp is not None else "",
                compact_float_text(last_value) if last_value is not None else "",
            ]
            for column, text in enumerate(values):
                item = QtWidgets.QTableWidgetItem(text)
                if column > 0:
                    item.setTextAlignment(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                self.stats_table.setItem(row_idx, column, item)

    def _update_capture_status(self):
        selected = self._selected_pvs()
        if not selected:
            self.capture_status.setText("No PVs selected")
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            return
        counts = [len(self._samples_by_pv.get(pv, []) or []) for pv in selected]
        if self._capture_active:
            self.capture_status.setText(
                f"Capturing: min {min(counts)} / {self._capture_goal}, max {max(counts)} / {self._capture_goal}"
            )
        else:
            self.capture_status.setText(
                f"Idle: min {min(counts)} / {self._capture_goal}, max {max(counts)} / {self._capture_goal}"
            )
        if self._capture_active and self._rolling_capture:
            self.capture_status.setText(f"Continuous capture · {min(counts)}–{max(counts)} buffered / PV · Stop to analyze")
        if self._capture_active and self._trigger is not None:
            self.capture_status.setText('Triggered — collecting post-trigger data' if self._trigger.fired else 'Armed — waiting for threshold crossing')
        self.start_btn.setEnabled(not self._capture_active)
        self.stop_btn.setEnabled(self._capture_active)
        for w in (*self.trigger_controls,*self.capture_edit_buttons,self.sample_count_spin,self.continuous,self.preset_btn,self.pv_edit):
            w.setEnabled(not self._capture_active)

    def _log(self, message):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")


def main():
    ap = argparse.ArgumentParser(description="Qt app for timestamp-derived DAQ and FFT analysis of EPICS PVs")
    ap.add_argument("--prefix", default="", help="Optional default IOC prefix for short PV names")
    ap.add_argument("--pv", action="append", default=[], help="Initial PV name (can be passed multiple times)")
    ap.add_argument("--timeout", type=float, default=2.0, help="EPICS timeout in seconds")
    ap.add_argument("--motion-trigger", default="", help="Preset rising Busy trigger PV; does not arm capture")
    args = ap.parse_args()

    app = QtWidgets.QApplication(sys.argv)
    window = DaqWindow(default_prefix=args.prefix, initial_pvs=args.pv, timeout=args.timeout)
    if args.motion_trigger:
        window.trigger_pv.setText(args.motion_trigger)
        window.trigger_edge.setCurrentIndex(0)
        window.trigger_threshold.setValue(0.5)
        window.trigger_enabled.setChecked(True)
    UiSettings("daq").attach_window(window)
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
