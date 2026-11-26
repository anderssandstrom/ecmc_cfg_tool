"""Source mode mappings for the native axis interface."""
SOURCE_COMMANDS = {"-EncSrcTyp-RB":"-EncSrcTyp-Cmd", "-TrjSrcTyp-RB":"-TrjSrcTyp-Cmd"}
def source_label(value):
    return {0:"Internal",1:"PLC"}.get(value,"—")
