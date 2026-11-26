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
