"""Shared, immutable IOC/axis identity and PV naming.

Resolution is read-only. Contexts are snapshots, never implicit permission
to write or move; callers must revalidate live state before motion.
"""
from dataclasses import dataclass
import re
from ecmc_epics import _join_prefix_pv


def combine_motor_record(axis_prefix, motor_name):
    """Preserve legacy handling of complete motor PV names."""
    a = str(axis_prefix or "").strip()
    m = str(motor_name or "").strip()
    if a and m:
        if m.startswith(a) or ":" in m:
            return m
        return _join_prefix_pv(a, m)
    return a or m


@dataclass(frozen=True)
class IocContext:
    prefix: str

    def __post_init__(self):
        prefix = str(self.prefix or "").strip().rstrip(":")
        if not prefix:
            raise ValueError("IOC prefix is required")
        object.__setattr__(self, "prefix", prefix)

    def pv(self, suffix):
        return _join_prefix_pv(self.prefix, suffix)

    def axis_config_pv(self, axis_id, suffix):
        axis_id = str(axis_id).strip()
        if not re.fullmatch(r"[0-9]+", axis_id):
            raise ValueError("Invalid axis ID")
        return self.pv(f"MCU-Cfg-AX{int(axis_id)}-{suffix}")


@dataclass(frozen=True)
class AxisContext:
    ioc: IocContext
    axis_id: int
    axis_prefix: str
    name: str

    @property
    def motor(self):
        return combine_motor_record(self.axis_prefix, self.name)

    def native_pv(self, suffix):
        if not self.motor:
            raise ValueError("Unresolved axis")
        return self.motor + "-" + suffix

    def motor_pv(self, field):
        if not self.motor:
            raise ValueError("Unresolved axis")
        return self.motor + "." + field

    @classmethod
    def resolve(cls, client, ioc, axis_id):
        if not isinstance(ioc, IocContext):
            ioc = IocContext(ioc)
        def read(suffix):
            value = client.get(ioc.axis_config_pv(axis_id, suffix), as_string=True)
            return str(value or "").strip().strip('"').strip()
        prefix, name = read("Pfx"), read("Nam")
        if not prefix or not name:
            raise ValueError("Incomplete axis identity")
        return cls(ioc, int(axis_id), prefix, name)
