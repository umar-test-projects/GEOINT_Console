"""The generated site, checked without a browser: pages match build_web.py,
their scripts parse, and every element a script looks up by id exists.

The id check is the one that matters most: a renamed or removed element is a
null lookup at runtime, which breaks the page for everyone and no Python test
would ever see.
"""
import re
import shutil
import subprocess

import pytest

import build_web

PAGES = {name: title for name, _label, _icon, title in build_web.PAGES}
INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.S)
LOOKUP = re.compile(r"""\$\(\s*["']([A-Za-z][\w-]*)["']\s*\)""")


def page(name):
    return (build_web.WEB / name).read_text(encoding="utf-8")


def test_shared_js_matches_generator():
    assert (build_web.WEB / "shared.js").read_text(encoding="utf-8") == build_web.SHARED_JS, \
        "web/shared.js is stale: run build_web.py"


@pytest.mark.parametrize("name", PAGES)
def test_page_matches_generator(name):
    body, script = build_web.BODIES[name]
    assert page(name) == build_web.shell(PAGES[name], body, script), \
        f"web/{name} is stale: run build_web.py"


@pytest.mark.parametrize("name", PAGES)
def test_every_looked_up_id_exists(name):
    html = page(name)
    missing = sorted({i for s in INLINE_SCRIPT.findall(html) for i in LOOKUP.findall(s)
                      if not re.search(r"""\bid\s*=\s*["']""" + re.escape(i) + r"""["']""", html)})
    assert not missing, f"{name} looks up ids that no element has: {missing}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", [*PAGES, "shared.js"])
def test_scripts_parse(name, tmp_path):
    source = (build_web.SHARED_JS if name == "shared.js"
              else "\n;\n".join(INLINE_SCRIPT.findall(page(name))))
    js = tmp_path / "page.js"
    js.write_text(source, encoding="utf-8")
    out = subprocess.run(["node", "--check", str(js)], capture_output=True, text=True)
    assert out.returncode == 0, f"{name}: {out.stderr}"
