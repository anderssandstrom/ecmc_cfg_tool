# ecmc Qt config/tuning tools

This folder now contains:

- `build_ecmc_command_catalog.py` (extracts commands from `ecmcCmdParser.c` + all `devEcmcSup/**/*.h` header docs)
- `build_cntrl_command_catalog.py` (controller-focused command catalog generator)
- `ecmc_commands.json` (generated command catalog)
- `ecmc_commands_cntrl.json` (generated controller command catalog)
- `ecmc_stream_qt.py` / `start.sh` (generic command/stream GUI)
- `ecmc_axis_cfg.py` / `start_axis.sh` (axis YAML config app)
- `ecmc_cntrl_qt.py` / `start_cntrl.sh` (controller tuning/config app)
- `ecmc_mtn_qt.py` / `start_mtn.sh` (motor-record motion app)
- `ecmc_iso230_qt.py` / `start_iso230.sh` (ISO 230-style bidirectional test app)
- `ecmc_daq_qt.py` / `start_daq.sh` (timestamp-derived DAQ viewer for numeric PVs with FFT analysis)

## Applications

### Stream

Use the stream app when you want a generic command console against the ecmc `CMD` / `QRY` PVs.

- Browses the generated command catalog and filters by command text, description, and category.
- Sends ad-hoc commands, triggers `PROC + Read QRY`, and shows the latest readback/result.
- Marks blocked commands from the local blocklist and can decode local error names when an error DB is present.
- Includes a multi-command editor for repeated command sequences.
- Opens the axis, Cntrl, and motion apps with the same IOC prefix.

### Axis

Use the axis app when you want to compare a YAML template with the live axis and push selected settings back to the IOC.

- Loads `axis_template.yaml` plus the command-map CSV and maps YAML leaves to ecmc axis commands.
- Reads and writes individual values, or uses `Read All`, `Write Filled`, and `Copy Read->Set` for bulk work.
- Tracks session changes and exports either changed values or current session-known values as YAML text.
- Opens selected rows in a popup where values can be polled and plotted over time.
- Opens the related Cntrl and motion windows for the same axis.

### Cntrl

Use the Cntrl app when tuning controller-related parameters for a real axis.

- Uses the filtered controller catalog so the table only shows controller-relevant command pairs.
- Supports multiple views: `Flat`, `Schematic`, `Diagram`, and `Controller Sketch`.
- Reads and writes paired set/read controller values and supports `Read All` plus `Copy Read->Set`.
- Exports changed controller values or current session-known values as YAML.
- Allows sketch-overlay calibration and layout saving for site-specific controller drawings.
- Only supports `REAL` axes; virtual axes are rejected.

### Motion

Use the motion app when driving the motor record directly for quick motion checks and small test sequences.

- Resolves the selected axis to its motor record and reads live motor status.
- Provides `Move To Position`, `Tweak`, endless `Jog`, and `Sequence/Scan (A<->B)` workflows.
- Shares VELO/ACCL/VMAX/ACCS settings across motion actions.
- Provides `Stop` and `Kill` controls for active motion.
- Can show compact trend graphs for `PosAct`, `PosSet`, and `PosErr`.

### ISO230

Use the ISO230 app when running an automated bidirectional positioning test against one or more reference PVs.

- Resolves the motor record, accepts up to five reference PVs, and lets one reference be selected for report calculations.
- Configures range, point count, cycles, settle time, samples per point, decimals, and approach margin.
- Estimates run duration before the test starts and executes the sweep automatically.
- Calculates ISO 230-style positioning metrics such as bidirectional accuracy, repeatability, and reversal values.
- Shows lower tabs for `Live graph progress`, `ISO230 summary`, and `Live status` during setup and execution.
- Overlays live actual motion and current target on the sweep schematic while the sequence runs.
- Previews and exports a Markdown report, exports CSV data, and saves or reloads full session files.
- Preview Report uses the currently loaded/measured dataset and does not auto-load demo data.
- Saved session loading also accepts older JSON session files that contain compatible `settings` / `measurements` payloads.
- Supports demo data loading and CLI demo report generation.
- Default settle time is `0 s`.

#### Checking axes with a stepper motor and a linear encoder

For these axes, it is possible to measure the mechanical performance between the motor and the encoder:

1. Run the axis in open loop mode. This can be selected in the `ecmc` expert panels.
2. Calibrate/reference the open loop counter by setting it to the same value as the encoder in the expert panel.
3. Start the ISO230 app and configure the scan, PVs, and motion parameters.
4. Select the encoder as the reference.
5. Run the sequence.

Important:
This measures the difference between the open loop counter and the encoder, not the accuracy of the complete system. It can still be useful when you want to evaluate the mechanical performance of the chain between the motor and the encoder. To judge the overall system performance, an external sensor must be used as the reference; see the next section for one example.


#### Using a Micro-Epsilon ILD2300 as the reference

**Warning: the sensor is equipped with a laser that can cause eye injuries. Do not look into the beam, and add covers to avoid reflections.**

1. Connect the sensor to the EtherCAT chain.
2. Add the following slave to the startup script:

```
${SCRIPTEXEC} ${ecmccfg_DIR}addSlave.cmd "HW_DESC=OptoILD2300_50mm, SLAVE_ID=<XX>"
```

Alternatively, you can use a compact controller and create a dedicated IOC just for the sensor.

For older versions of `ecmc`, add the following line to the startup script to get the correct panel in the hw overview:

```bash
# Example for master 0 and slave 21
afterInit("dbpf c6025a-0:m0s021-PnlTyp OptoILD2300_XXmm")
```

### DAQ

Use the DAQ app when you want to capture one or more scalar numeric PVs and inspect both the time-domain signal and its frequency content.

- Captures a configurable number of samples per PV.
- Derives the effective sample rate from the captured timestamps instead of assuming a fixed rate.
- Uses EPICS monitor callbacks when `pyepics` is available, with a timer-poll fallback for slower environments.
- Shows the time-domain traces in the main window and the FFT spectrum in a separate frequency window.
- Displays per-PV capture span, effective sample rate, FFT size, and dominant spectral peak.

## Generate/update command catalog

Run from this repo root:

```bash
./build_ecmc_command_catalog.py --repo-root <path-to-ecmc-repo> --out ./ecmc_commands.json
```

## Run GUIs

### Generic stream GUI

```bash
./start.sh IOC:ECMC
```

This sets:

- Command PV: `<prefix>:MCU-Cmd.AOUT`
- Readback PV: `<prefix>:MCU-Cmd.AINP`

Example: `./start.sh MYIOC:SYS1` uses `MYIOC:SYS1:MCU-Cmd.AOUT` and `MYIOC:SYS1:MCU-Cmd.AINP`.

### Axis / Controller / Motion / ISO230 apps

All four app launchers accept:

- `./start_*.sh <IOC prefix> <axis id>`
- `./start_*.sh <IOC prefix> <motor name>`

Examples:

```bash
./start_axis.sh IOC:ECMC 3
./start_cntrl.sh IOC:ECMC M1
./start_mtn.sh IOC:ECMC Axis1
./start_iso230.sh IOC:ECMC 3
```

The apps resolve axis IDs from IOC config PVs when a motor name/full motor PV is provided.

### DAQ app

DAQ launcher accepts:

- `./start_daq.sh <IOC prefix>`
- `./start_daq.sh <IOC prefix> <pv1> <pv2> ...`

Example:

```bash
./start_daq.sh IOC:ECMC AXIS7-PosAct AXIS7-Enc01-PosAct
```

## Axis selection behavior (axis / controller / motion / ISO230 apps)

- Top-right axis selector is a dropdown populated from IOC axis config:
  - `<prefix>:MCU-Cfg-AX-FrstObjId`
  - `<prefix>:MCU-Cfg-AX<id>-NxtObjId`
  - axis motor info from `-Pfx` and `-Nam`
- The first dropdown item is a checkable `Open New Instance`
  - unchecked: apply selected axis in current window
  - checked: open a new app instance for selected axis
- Axis ID edit + `Apply Axis` are in each app's expandable config section
- On startup, apps first probe one PV (`...AX<axis>-Pfx`) before doing heavier startup actions
  - if probe fails/empty, the app prompts axis selection via the dropdown

Controller app specifics:

- Controller commands are only valid for `REAL` axes
- Virtual axes are blocked in controller axis selection
- If controller app starts on a virtual axis, the classic axis picker dialog opens

## caQtDM buttons

- `caqtdm Main` button in the config section of the axis, controller, and motion apps:
  - launches `ecmcMain.ui` with macro `IOC=<IOC prefix>`
- `caqtdm Axis` button:
  - in motion app (below axis selector)
  - in controller app (next to `Copy Read->Set`)
  - launches `ecmcAxis.ui` with macros:
    - `DEV=<motor prefix>` (trailing `:` removed)
    - `IOC=<IOC prefix>`
    - `Axis=<axis name>`
    - `AX_ID=<axis id>`

## Dependencies

One Qt binding:

- `PyQt5` or `PySide6`

One EPICS client backend:

- `pyepics` (preferred), or
- EPICS CLI tools in PATH: `caput` and `caget`

## Notes

- `CMD` PV is used to send arbitrary command strings.
- Readback PV is read after triggering the parent record `.PROC` (works for field PVs like `.AINP`).
- The command browser templates are extracted from `devEcmcSup/com/ecmcCmdParser.c`.
- Descriptions are matched from all header doc blocks under `devEcmcSup/` when available.
- caQtDM launchers use `bash -lc` to avoid wrapper-script `Exec format error` on some systems.
