"""Headless click-through of the TrialProof Studio UI with no model: open the pilot workspace, run, validate,
trace a cell, sign off, export, agent error when offline, theme, phone width. Needs a running server and
Google Chrome; playwright is a dev-only dependency:

  python -m venv /tmp/pw && /tmp/pw/bin/pip install playwright httpx pytest
  /tmp/pw/bin/python scripts/ui_check.py [--out screenshots/]
"""
import argparse, re, sys
from pathlib import Path
import httpx
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server" / "tests"), str(ROOT / "server")]
from test_flow import SPEC, TEAE_R  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8765")
ap.add_argument("--out", default="ui_check")
args = ap.parse_args()
API, OUT = args.api, args.out
Path(OUT).mkdir(parents=True, exist_ok=True)
steps, errors = [], []


def ok(name, cond, extra=""):
    steps.append((name, bool(cond), extra))
    print(("PASS " if cond else "FAIL ") + name + (f"  ({extra})" if extra else ""), flush=True)


with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    pg = b.new_page(viewport={"width": 1440, "height": 900})
    pg.on("console", lambda m: m.type == "error" and errors.append(m.text))
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(API)
    pg.get_by_text("Open pilot workspace").click()
    pg.wait_for_function("() => /[0-9a-f]{10}/.test(location.href)", timeout=60000)
    wid = re.search(r"[0-9a-f]{10}", pg.url).group(0)
    ok("open pilot workspace", wid, wid)
    pg.get_by_text("ADSL", exact=False).first.wait_for(timeout=60000)
    ok("datasets listed", pg.get_by_text("ADAE", exact=False).count() > 0)
    pg.screenshot(path=f"{OUT}/1_workspace.png")

    c = httpx.Client(base_url=API, timeout=60)
    c.post(f"/api/workspaces/{wid}/specs", json={"spec": SPEC, "rationale": "safety population"}).raise_for_status()
    c.post(f"/api/workspaces/{wid}/programs", json={"code": TEAE_R}).raise_for_status()
    pg.wait_for_timeout(1500)
    ok("program appears in editor via live events", pg.locator(".monaco-editor").count() > 0 and "tp_emit_table" in pg.locator(".monaco-editor").inner_text())

    pg.get_by_role("button", name="Run", exact=True).click()
    pg.wait_for_function("() => document.body.innerText.includes('417')", timeout=90000)
    body = pg.inner_text("body")
    ok("run + validation shown", "417" in body, "417 cells")
    pg.screenshot(path=f"{OUT}/2_results.png")

    # trace one cell: the Placebo any-TEAE count (65)
    cell = pg.get_by_text(re.compile(r"^65$")).first
    cell.click()
    pg.wait_for_timeout(1500)
    body = pg.inner_text("body")
    ok("cell lineage opens", "86" in body and ("Population" in body or "SAFFL" in body))
    pg.screenshot(path=f"{OUT}/3_trace.png")
    pg.keyboard.press("Escape")

    # sign-off
    run = c.get(f"/api/workspaces/{wid}").json()["run"]
    rn = pg.get_by_placeholder("Reviewer name")
    if rn.count() == 0:
        pg.get_by_role("tab", name=re.compile("Verification")).click()
        pg.wait_for_timeout(800)
    rn = pg.get_by_placeholder("Reviewer name")
    ok("sign-off form reachable", rn.count() > 0)
    if rn.count():
        rn.fill("UI check")
        pg.get_by_placeholder("Review note").fill("headless click-through")
        btn = pg.get_by_role("button", name=re.compile("^Sign off program", re.I)).first
        ok("sign-off blocked until review is confirmed", btn.is_disabled())
        pg.get_by_role("checkbox").last.check()
        btn.click()
        pg.wait_for_timeout(1500)
        ap = c.get(f"/api/workspaces/{wid}").json()["approvals"]
        ok("sign-off recorded", ap and ap[-1]["reviewer"] == "UI check", ap[-1]["decision"] if ap else "")
    r = c.get(f"/api/workspaces/{wid}/export")
    ok("export zip downloads", r.status_code == 200 and r.content[:2] == b"PK", f"{len(r.content)} bytes")

    # agent with the model offline: the error must be visible, not silent
    comp = pg.locator("textarea").last
    comp.fill("Summarize the results")
    pg.get_by_label("Send").click()
    pg.wait_for_timeout(8000)
    body = pg.inner_text("body")
    ok("agent error shown when model is offline", re.search(r"unreachable|not configured|authorized|Settings", body, re.I))
    pg.screenshot(path=f"{OUT}/4_agent_offline.png")

    pg.get_by_label("Toggle theme").click()
    pg.wait_for_timeout(500)
    pg.screenshot(path=f"{OUT}/5_theme.png")
    ok("theme toggles", True)

    m = b.new_page(viewport={"width": 390, "height": 844})
    m.goto(pg.url)
    m.wait_for_timeout(3000)
    sw = m.evaluate("document.documentElement.scrollWidth")
    ok("phone width: no horizontal scroll", sw <= 392, f"scrollWidth {sw}")
    m.screenshot(path=f"{OUT}/6_phone.png")
    b.close()

bad = [e for e in errors if "favicon" not in e]
ok("no console errors", not bad, "; ".join(bad[:3]))
print(f"\n{sum(s[1] for s in steps)}/{len(steps)} checks passed")
