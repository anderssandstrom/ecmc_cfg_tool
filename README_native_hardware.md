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
