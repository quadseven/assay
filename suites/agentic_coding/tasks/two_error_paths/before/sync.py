class CorruptConfig(Exception):
    pass


def render(entry):
    """Render one entry. Raises ValueError on a malformed entry."""
    if not isinstance(entry, dict) or "name" not in entry:
        raise ValueError("entry has no 'name'")
    return {"name": entry["name"], "enabled": True}


def sync(entries, *, read_existing):
    """Merge `entries` into the stored config.

    Returns {"written": [...], "errors": [...]}.
    """
    errors = []
    try:
        existing = read_existing()
    except CorruptConfig as e:
        errors.append(str(e))
        existing = {}

    written = []
    for entry in entries:
        try:
            rendered = render(entry)
        except ValueError as e:
            errors.append(str(e))
            continue
        existing[rendered["name"]] = rendered
        written.append(rendered["name"])
    return {"written": written, "errors": errors}
