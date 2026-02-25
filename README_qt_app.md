# ecmc Qt config/tuning tools

This folder now contains:

- `ecmcCmd.proto` and `ecmcCmd.db` (generic StreamDevice records)
- `startup_example.cmd` (IOC shell snippet)
- `build_ecmc_command_catalog.py` (extracts commands from `ecmcCmdParser.c` + all `devEcmcSup/**/*.h` header docs)
- `build_cntrl_command_catalog.py` (controller-focused command catalog generator)
- `ecmc_commands.json` (generated command catalog)
- `ecmc_commands_cntrl.json` (generated controller command catalog)
- `ecmc_stream_qt.py` (Qt GUI to send commands to EPICS PVs)
- `ecmc_axis_cfg.py` / `start_axis.sh` (axis YAML config app)
- `ecmc_cntrl_qt.py` / `start_cntrl.sh` (controller tuning/config app)
- `ecmc_mtn_qt.py` / `start_mtn.sh` (motor-record motion app)
- `ecmc_favorites.json` (saved favorites for quick command reuse)

## Generate/update command catalog

Run from the ecmc repo root:

```bash
../ecmc_cfg_stream/build_ecmc_command_catalog.py --repo-root . --out ../ecmc_cfg_stream/ecmc_commands.json
```

## Run GUIs

### Generic stream GUI

```bash
cd ../ecmc_cfg_stream
./start.sh IOC:ECMC
```

This sets:

- Command PV: `<prefix>:MCU-Cmd.AOUT`
- Readback PV: `<prefix>:MCU-Cmd.AINP`

Example: `./start.sh MYIOC:SYS1` uses `MYIOC:SYS1:MCU-Cmd.AOUT` and `MYIOC:SYS1:MCU-Cmd.AINP`.

### Axis / Controller / Motion apps

All three app launchers accept:

- `./start_*.sh <IOC prefix> <axis id>`
- `./start_*.sh <IOC prefix> <motor name>`

Examples:

```bash
./start_axis.sh IOC:ECMC 3
./start_cntrl.sh IOC:ECMC M1
./start_mtn.sh IOC:ECMC Axis1
```

The apps resolve axis IDs from IOC config PVs when a motor name/full motor PV is provided.

## Axis selection behavior (axis / controller / motion apps)

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

- `caqtdm Main` button in the config section of all three apps:
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
- Favorites are loaded at startup and saved on add/remove (or with `Save Favorites`).
- caQtDM launchers use `bash -lc` to avoid wrapper-script `Exec format error` on some systems.
