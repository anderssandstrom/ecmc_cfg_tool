# ecmc Stream Qt client

This folder now contains:

- `ecmcCmd.proto` and `ecmcCmd.db` (generic StreamDevice records)
- `startup_example.cmd` (IOC shell snippet)
- `build_ecmc_command_catalog.py` (extracts commands from `ecmcCmdParser.c` + all `devEcmcSup/**/*.h` header docs)
- `ecmc_commands.json` (generated command catalog)
- `ecmc_stream_qt.py` (Qt GUI to send commands to EPICS PVs)
- `ecmc_favorites.json` (saved favorites for quick command reuse)

## Generate/update command catalog

Run from the ecmc repo root:

```bash
../ecmc_cfg_stream/build_ecmc_command_catalog.py --repo-root . --out ../ecmc_cfg_stream/ecmc_commands.json
```

## Run GUI

```bash
cd ../ecmc_cfg_stream
./start.sh IOC:ECMC
```

This sets:

- Command PV: `<prefix>:MCU-Cmd.AOUT`
- Readback PV: `<prefix>:MCU-Cmd.AINP`

Example: `./start.sh MYIOC:SYS1` uses `MYIOC:SYS1:MCU-Cmd.AOUT` and `MYIOC:SYS1:MCU-Cmd.AINP`.

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
