import re
from pathlib import Path

JS = Path(__file__).parent.parent / "market_tracker" / "static" / "app.js"


def test_no_duplicate_top_level_names_in_app_js():
    """A second top-level function with the same name silently replaces the first (this broke
    Home's buying power once), so every top-level function and const must be unique."""
    names = re.findall(r"^(?:async )?function ([A-Za-z_$][\w$]*)|^const ([A-Za-z_$][\w$]*) =", JS.read_text(), re.M)
    flat = [a or b for a, b in names]
    dupes = sorted({n for n in flat if flat.count(n) > 1})
    assert not dupes, dupes


def test_every_element_the_script_binds_at_load_exists():
    """$("#id").addEventListener at the top level throws on a missing element and stops the page."""
    html = (JS.parent / "index.html").read_text()
    ids = set(re.findall(r'id="([^"]+)"', html))
    bound = set(re.findall(r'^\$\("#([\w-]+)"\)\.addEventListener', JS.read_text(), re.M))
    missing = sorted(bound - ids)
    assert not missing, missing
