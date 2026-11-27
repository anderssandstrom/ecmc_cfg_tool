# Native hardware diagnostics (prototype)

The IOC Navigator opens native read-only slave, hardware overview and axis hardware tabs.
No caQtDM is required for these three actions. Select a device, refresh its PV snapshot,
or send selected successfully read PVs to DAQ. Demo mode never reads the network.

The initial catalog was extracted from channel properties of selected ecmccfg
v11.2.1_RC1 Qt panels. CH and DRV templates were expanded conservatively to
channels 1 and 2; not all candidate PVs exist on every variant. Missing PVs
are displayed as unavailable. This is not a PDO or SDO enumeration, does not
decode status bits, and does not perform writes. Other navigator actions still
may require caQtDM. Polling and a general recursive catalog builder are future work.

Run catalog tests with python -m unittest test_ecmc_hardware_catalog.


## Native commissioning and validation

The axis expert has separate Motion, Encoders, Homing, Limits, Trajectory,
Controller and Diagnostics tabs. Drive enable reflects the native enable
command readback or motor record CNEN. Encoder selection and source changes
require a disabled stationary drive. Jog uses a positive speed and a held
direction button; release, disarming, hiding or app deactivation requests stop.
A cancelled in-flight start also queues a follow-up stop. These are supervisory
software commands, not safety-rated controls; a network failure or process
termination can prevent a stop from reaching the IOC.

Plugin discovery records only identify linked-list objects. The plugin panel
supports explicit read-only IOC-local scalar PV monitoring (up to 32 signals)
and trends; it does not infer plugin-specific controls or schemas.

Before live deployment, verify on an unloaded test axis: enable/disable,
encoder selection, both source selectors, homing, limit interlocks, positive
and negative jog, rapid press/release, tab hide, disarm, STOP/KILL, and loss
of CA connectivity. Independently verify standstill and hardware safety.

## PLC metadata and engineering tools

Open a PLC's native diagnostics tab, then **Compiled source / variables** and
**Load compiled PLC source**. The read-only browser highlights the source captured
at compilation and filters registered local/global variable names. Its worker
uses the passive `PLCnn-DiagCmd`, `DiagStat`, and `DiagData` records; both status
and payload tokens must match. Requests are limited locally per prefix and in
ECMC across all PLCs in the IOC to one per second. Busy/replaced/unavailable
responses show an error and require a manual refresh. Existing PV diagnostics
and their trend actions remain in the first subtab; arbitrary live PLC values
are not sampled. Pyepics and the matching ECMC/ECMCCFG records are required.

The **Engineering tools** menu provides:

- **Custom / multi-IOC dashboard**: add full PV names from multiple IOCs, save/load
  a versioned JSON signal list, select rows to trend, and export displayed values,
  connection/severity, event timestamps and report time as CSV. Maximum 256 rows.
  This is latest-event monitoring, not a synchronized cross-IOC acquisition.
  Hidden dashboards release subscriptions; showing them reconnects. Demo mode
  does not connect to Channel Access.
- **Axis parameter comparison**: manually read 19 exposed native configuration
  fields for two full axis PV bases. Includes limits/enables, velocity,
  acceleration/deceleration, PID, source/encoder/drive settings and units, with
  numeric deltas and absolute tolerance. Missing/nonfinite values are unavailable,
  not equal. Native values use dial coordinates; no write or automatic conversion
  to motor user coordinates is performed. This is not a complete YAML/SDO compare.
- **Physical EtherCAT port map**: load explicit node/port connections. No physical
  adjacency is inferred from slave order. This is an imported wiring map, not
  automatic/live cable discovery. Links require unique endpoint ports (0–3).

Dashboard JSON example:

```json
{"version":1,"signals":[{"pv":"IOC:A:MCU-ErrId","label":"IOC A error"},{"pv":"IOC:B:MCU-ErrId","label":"IOC B error"}]}
```

Physical port map example (replace with verified site wiring):

```json
{"version":1,"nodes":[{"id":"master","label":"Master 0"},{"id":"s0","label":"Coupler"}],"links":[{"from":"master","from_port":0,"to":"s0","to_port":0}]}
```

Dashboard and hardware-trend windows share one reference-counted, read-only CA
monitor per full PV name. A lease exposes snapshots and release only, no put.
Closing one trend cannot disconnect another consumer; disconnect clears stale
values. Existing legacy panels retain their previous subscription implementation.
Trend history retains its existing bounded queues and CSV export. New dashboard
reports export the displayed latest values and do not represent a history capture.

### Validation and performance scope

Offline regression tests cover metadata parsing/token collisions/failed writes,
shared monitor lifetime/disconnection, two simultaneous trend windows, dashboard
validation/lifecycle, comparison unknowns/tolerance, explicit topology validation,
and Qt Navigator/PLC tab integration. The full local suite passed with Python
3.11/PyQt5; live CA behavior, real IOC metadata transfer, hardware topology, and RT
latency have not been tested. UI timers are 100 ms while awaiting a worker and
500 ms for visible dashboards; no IOC cyclic code was added. One worker per PLC
browser/comparison bounds concurrent work within each panel. Pyepics batch reads
have finite connection and per-PV timeouts (not a single total refresh deadline):
https://pyepics.github.io/pyepics/overview.html#caget-many

The pre-existing CppLogic discovery failure was fixed by supporting both ECMCCFG
`CPPnn` and direct ECMC `CppLogicN` record names. The generic expert-separation
fixture now uses EL7041, since EL7062 intentionally promotes drive controls into
its overview. These two failures were reproduced on the original branch head.


### Incident capture

In IOC Navigator, discover the IOC and click **Capture incident** to save a JSON file. **Open incident** opens a read-only offline tab (including legacy snapshots). The file extends the existing discovery snapshot with health, cached health PV values and monitor ages, IOC-scoped transition history and bounded samples from already-open hardware trends. Discovery includes versions as of last refresh. No additional CA requests, SDO reads or ECMC changes are made during capture. Values are asynchronous, not an RT snapshot. Demo captures are marked. The viewer initially presents read-only JSON sections; graphical replay and automatic triggering are not yet supported. Navigator health monitors now use the shared read-only pool.

## Database-derived hardware catalogue

Generate a catalogue from a local ECMCCFG checkout (the root or its qt directory):

```sh
python build_hardware_catalog.py /path/to/ecmccfg --source v11.2.1_RC1 --source-commit YOUR_COMMIT --output /tmp/ecmc-hardware.json
export ECMC_HARDWARE_CATALOG=/tmp/ecmc-hardware.json
```

The generator merges explicit database substitution rows and resolvable record names with Qt panels. Database-only signals are read-only and missing PVs remain unavailable. Unresolved macros and missing/ambiguous templates are not guessed; inspect database_warnings in the generated JSON. This does not enumerate raw PDOs or guarantee coverage of dynamically loaded databases. The bundled legacy catalogue remains the default until regenerated. Restart the application after changing the catalogue.
