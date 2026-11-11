"""Playwright run of the screen_qa select-beat screen against the Windows server (flet 0.86.5)
through an SSH tunnel: render, approve, pick MP4 in the P1 /moments_review tab, see the chip
appear (listener + run_task thread-safety), back to card, add a still through the file chooser."""
import json, subprocess, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8561"
OUT = Path("/tmp/p3v_shots"); OUT.mkdir(exist_ok=True)
PROJ = r"D:\code\cbp-video-test-p3v\projects_ui\ui_screen"
RESULTS = []

def ssh_type(rel: str) -> str:
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=20", "winbox-lan", f"type {PROJ}\\{rel}"],
                       capture_output=True, text=True)
    return r.stdout

def jread(rel):
    try:
        return json.loads(ssh_type(rel))
    except ValueError:
        return {}

def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""), flush=True)

def btn(page, text, nth=0):
    return page.locator("flt-semantics[role=button]", has_text=text).nth(nth)

def has_btn(page, text, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if page.locator("flt-semantics[role=button]", has_text=text).count() > 0:
            return True
        time.sleep(0.4)
    return False

def no_btn(page, text, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if page.locator("flt-semantics[role=button]", has_text=text).count() == 0:
            return True
        time.sleep(0.4)
    return False

def px(page, x, y):
    """The most 'green' pixel on a short horizontal scan across x±6 (the card border is 1px wide)."""
    from io import BytesIO
    from PIL import Image
    im = Image.open(BytesIO(page.screenshot())).convert("RGB")
    pts = [im.getpixel((x + d, y)) for d in range(-6, 7)]
    return max(pts, key=lambda c: c[1] - c[0])

def has_text(page, text, timeout=12.0):
    """The Flutter semantics tree lags a rebuild by a second or two — poll for it."""
    end = time.time() + timeout
    while time.time() < end:
        if page.locator("flt-semantics", has_text=text).count() > 0:
            return True
        time.sleep(0.4)
    return False

def gone(page, text, timeout=12.0):
    end = time.time() + timeout
    while time.time() < end:
        if page.locator("flt-semantics", has_text=text).count() == 0:
            return True
        time.sleep(0.4)
    return False

with sync_playwright() as p:
    b = p.chromium.launch(channel="chrome", headless=True)
    ctx = b.new_context(viewport={"width": 1400, "height": 900})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)[:200]))
    page.goto(BASE + "/", wait_until="load")
    time.sleep(8)
    page.evaluate("document.querySelector('flt-semantics-placeholder').click()")
    time.sleep(2)
    page.screenshot(path=str(OUT / "10_initial.png"))
    check("screen renders: Approve + 6 beat action rows", has_btn(page, "Approve") and
          page.locator("flt-semantics[role=button]", has_text="Thẻ chữ").count() == 6)
    border0 = px(page, 269, 280)
    check("6 'Chọn MP4' buttons (flag ON)", page.locator("flt-semantics[role=button]", has_text="Chọn MP4").count() == 6)

    # ── Approve ───────────────────────────────────────────────────────────────
    btn(page, "Approve").click(); time.sleep(2)
    locks = jread(r"review\locks.json")
    check("Approve writes locks.json (approved + narration_sha1)", locks.get("approved") is True and locks.get("narration_sha1"),
          str({k: locks.get(k) for k in ("approved", "approved_at")}))
    check("button label flips to Un-approve after approve", has_btn(page, "Un-approve"))
    page.screenshot(path=str(OUT / "11_approved.png"))

    # ── Chọn MP4 → the P1 tab ────────────────────────────────────────────────
    with ctx.expect_page() as pop:
        btn(page, "Chọn MP4", 0).click()
    tab = pop.value
    tab.wait_for_load_state("load"); time.sleep(1)
    url = tab.url
    check("P1 tab opened for beat 1:0 with url-encoded query",
          "/moments_review?project=ui_screen&beat=1%3A0&q=Scott%20Lang%20van%20quantum%20tunnel" in url, url)
    tab.screenshot(path=str(OUT / "12_moments_review.png"))
    check("P1 tab lists the cached shortlist", "Fake candidate 1" in tab.content())
    tab.locator("button.pick-btn").first.click(); time.sleep(2)
    manifest = jread(r"review\clips\clips.json")
    entry = next((c for c in manifest.get("clips", []) if c.get("beat") == "1:0"), {})
    check("pick wrote clips.json entry on beat 1:0", entry.get("id") == "vid1AAAAAA", str(entry)[:160])
    tab.close()

    # ── the Flet page updates by itself (listener → page.run_task) ───────────
    time.sleep(3)
    page.screenshot(path=str(OUT / "13_after_pick.png"))
    check("card 1:0 switches to 'Đổi MP4' without a reload (listener -> run_task)", has_btn(page, "Đổi MP4"))
    after = px(page, 269, 280)
    check("card 1:0 border turns green (clip picked)", after != border0 and after[1] > after[0] + 40, f"{border0} -> {after}")
    check("approval withdrawn by the late pick (button back to Approve)", no_btn(page, "Un-approve"))
    locks = jread(r"review\locks.json")
    check("locks.json approval is off", locks.get("approved") is False)

    # ── back to a card ───────────────────────────────────────────────────────
    btn(page, "Thẻ chữ", 0).click(); time.sleep(2)
    manifest = jread(r"review\clips\clips.json")
    check("'Thẻ chữ' removed the clip", not [c for c in manifest.get("clips", []) if c.get("beat") == "1:0"])
    check("card back to 'Chọn MP4' (automatic text card)", no_btn(page, "Đổi MP4") and px(page, 269, 280) == border0)

    # ── still through the file chooser ───────────────────────────────────────
    frame = OUT / "frame.png"
    if not frame.exists():
        subprocess.run(["python3", "-c", "from PIL import Image; Image.new('RGB',(1280,720),(40,90,160)).save('%s')" % frame], check=True)
    try:
        with page.expect_file_chooser(timeout=10000) as fc:
            btn(page, "Ảnh", 2).click()              # beat 2:0
        fc.value.set_files(str(frame))
        time.sleep(4)
        locks = jread(r"review\locks.json")
        still = (locks.get("locks") or {}).get("2:0", {})
        check("still locked to beat 2:0 via locks.json", bool(still.get("custom_image")), str(still))
        side = jread(r"review\custom\custom_images.json")
        check("still is in the custom-image sidecar", any(i.get("file") == still.get("custom_image") for i in side.get("images", [])))
        check("beat 2:0 button now reads 'Đổi ảnh'", has_btn(page, "Đổi ảnh"))
        page.screenshot(path=str(OUT / "14_still.png"))
    except Exception as exc:
        check("file chooser flow", False, repr(exc)[:200])
        page.screenshot(path=str(OUT / "14_still_failed.png"))

    # ── Làm mới + continue ──────────────────────────────────────────────────
    btn(page, "Làm mới").click(); time.sleep(2)
    btn(page, "Approve").click(); time.sleep(2)
    btn(page, "Continue").click(); time.sleep(3)
    page.screenshot(path=str(OUT / "15_continue.png"))
    st = json.loads(ssh_type("state.json") or "{}")
    check("Continue → TTS moved the app to stage 6 (not 8)", st.get("current_stage") == 6, str(st.get("current_stage")))
    check("no JS page errors", not errors, "; ".join(errors)[:200])
    b.close()

bad = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(bad)}/{len(RESULTS)} checks passed")
sys.exit(1 if bad else 0)
