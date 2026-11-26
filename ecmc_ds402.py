"""CiA 402 state decoding (statusword masks, not controlword inference)."""
STATES = (
    (0x004F, 0x0000, "Not ready to switch on"),
    (0x004F, 0x0040, "Switch on disabled"),
    (0x006F, 0x0021, "Ready to switch on"),
    (0x006F, 0x0023, "Switched on"),
    (0x006F, 0x0027, "Operation enabled"),
    (0x006F, 0x0007, "Quick stop active"),
    (0x004F, 0x000F, "Fault reaction active"),
    (0x004F, 0x0008, "Fault"),
)

def word_number(value):
    try:
        s = str(value).strip()
        n = int(s, 16) if s.lower().startswith("0x") else int(s)
        return n if 0 <= n <= 65535 else None
    except (ValueError, TypeError):
        return None

def decode_state(value):
    word = word_number(value)
    if word is None:
        return "Unavailable"
    return next((state for mask, pattern, state in STATES if word & mask == pattern), "Unknown state")

CONTROL_BITS = {0: "Switch on", 1: "Enable voltage", 2: "Quick stop (0 = request)",
                3: "Enable operation", 7: "Fault reset", 8: "Halt"}
STATUS_BITS = {0: "Ready to switch on", 1: "Switched on", 2: "Operation enabled",
               3: "Fault", 4: "Voltage enabled", 5: "Quick stop (0 = active)",
               6: "Switch on disabled", 7: "Warning", 9: "Remote",
               10: "Target reached", 11: "Internal limit active"}

def bit_label(bit, status=False):
    return (STATUS_BITS if status else CONTROL_BITS).get(bit, "Mode/device specific or reserved")

def edit_controlword(original, changes):
    """Only explicitly selected standard control bits are modified."""
    value = word_number(original)
    if value is None:
        raise ValueError("Controlword unavailable")
    for bit, enabled in changes.items():
        if bit not in CONTROL_BITS:
            raise ValueError("Unsupported controlword bit")
        value = value | (1 << bit) if enabled else value & ~(1 << bit)
    return value
