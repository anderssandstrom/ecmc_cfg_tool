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
