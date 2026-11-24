"""Shared Motor Record operations; GUI logging and PV binding are injected.

These operations request motion changes, not safety-rated stops. A successful
write is not proof of physical standstill.
"""


def set_move_params(put, log, velo, accl, accs=None, vmax=None):
    for field, value in (("VMAX", vmax), ("ACCS", accs)):
        if value is not None:
            try:
                put(field, value)
            except Exception as ex:
                log(f"{field} unavailable ({ex})")
    put("VELO", velo)
    put("ACCL", accl)


def set_jog_params(put, log, velo, accl, accs=None, vmax=None):
    for field, value in (("VMAX", vmax), ("ACCS", accs)):
        if value is not None:
            try:
                put(field, value)
            except Exception as ex:
                log(f"{field} unavailable ({ex})")
    put("ACCL", accl)
    try:
        put("JVEL", velo)
    except Exception as ex:
        log(f"JVEL unavailable, using VELO ({ex})")
        put("VELO", velo)


def clear_jog(put):
    for field in ("JOGF", "JOGR"):
        try:
            put(field, 0, quiet=True)
        except Exception:
            pass


def request_stop(put, log):
    clear_jog(put)
    try:
        put("STOP", 1)
    except Exception as ex:
        log(f"STOP field failed ({ex}), trying SPMG=0")
        try:
            put("SPMG", 0)
        except Exception as fallback:
            raise RuntimeError(f"STOP and SPMG failed: {fallback}") from fallback


def request_kill(put):
    clear_jog(put)
    try:
        put("STOP", 1, quiet=True)
    except Exception:
        pass
    put("CNEN", 0)
