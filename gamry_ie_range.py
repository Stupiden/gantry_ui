"""Hardware-specific Gamry I/E range choices for Tk controls."""


def choices(pstat):
    """Return display labels in ``enum — hardware label`` form."""
    result = []
    try:
        lower = int(pstat.ie_range_lower_limit())
        upper = int(pstat.ie_range_upper_limit())
        for range_id in range(lower, upper + 1):
            label = str(pstat.ie_range_label(range_id)).strip()
            if label and label.upper() != "N/A":
                result.append("{} — {}".format(range_id, label))
    except Exception:
        # The official hardware table defines enumerators 1 through 13.
        result = [str(range_id) for range_id in range(1, 14)]
    return result


def enum_value(display_value):
    """Convert a displayed choice back to the integer Toolkit enumerator."""
    return int(str(display_value).split("—", 1)[0].strip())


def display_value(range_id, available_choices):
    wanted = str(int(range_id))
    return next(
        (item for item in available_choices
         if item.split("—", 1)[0].strip() == wanted),
        wanted,
    )
