"""Homing procedures from ecmcAxis.db; manual set-position is not homing."""
HOME_PROCEDURES = (
    (1,"Backward limit"),(2,"Forward limit"),(3,"Home switch via backward limit"),
    (4,"Home switch via forward limit"),(5,"Home switch midpoint via backward limit"),
    (6,"Home switch midpoint via forward limit"),(7,"Home switch approach backward"),
    (8,"Home switch approach forward"),(9,"Home switch midpoint approach backward"),
    (10,"Home switch midpoint approach forward"),(11,"Index via backward limit"),
    (12,"Index via forward limit"),(13,"Home switch left/right"),(14,"Use encoder configuration"),
)
def home_procedure_label(value):
    return dict(HOME_PROCEDURES).get(value,"Unavailable / not a homing procedure")
