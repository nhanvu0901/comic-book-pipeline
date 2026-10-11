"""Pick an MP4 for one review beat through the real UI: card button -> new tab -> YT.Player -> 'Dùng từ đây'."""
import json, sys, time, subprocess
sys.path.insert(0, "/tmp/p1_work")
from ui import Session, EVID, BASE

beat = sys.argv[1]                      # e.g. "2:1"
scene_no, chip = sys.argv[2], sys.argv[3]     # how to find its card on screen: scene number + its duration chip, e.g. 02 3.8s
cand_arg = sys.argv[4] if len(sys.argv) > 4 else '0'
cand_pick = int(cand_arg) if cand_arg.lstrip('-').isdigit() else cand_arg     # candidate index, -1 = shortest, or a video id
tag = beat.replace(":", "_")

def say(m):
    print(f"{time.strftime('%H:%M:%S')} {m}", flush=True)

def find_card(s, p):
    """The semantics group of the card whose header shows scene number + duration chip."""
    for g in s.labels(p):
        parts = g["label"].split("\n")
        if parts[0].startswith("Thời lượng beat") and len(parts) > 3 and parts[1] == scene_no and chip in parts:
            return g
    return None

with Session() as s:
    flet = s.open_flet()
    # bring the card's header on screen: wheel to the top, then step down until it is in view
    for _ in range(40):
        flet.mouse.move(600, 500); flet.mouse.wheel(0, -1500); time.sleep(0.15)
    card = None
    for _ in range(60):
        card = find_card(s, flet)
        if card and 120 < card["r"][1] < 650:
            break
        flet.mouse.move(600, 500); flet.mouse.wheel(0, 300); time.sleep(0.35)
    assert card, f"card for scene {scene_no} chip {chip} not found"
    cx, cy, cw, ch = card["r"]
    # The header's icon row is [add image][Dùng MP4][(undo)][merge][trash] and the chips to its right vary in
    # width per card, so locate the icons on the pixels: the 2nd bright glyph cluster right of x=640.
    import io
    import numpy as np
    from PIL import Image
    img = Image.open(io.BytesIO(flet.screenshot())).convert("L")
    arr = np.asarray(img).astype(int)
    band = arr[cy + 22: cy + 50, 640: cx + cw]
    cols = (band.max(axis=0) > 90)
    clusters, start = [], None
    for i, on in enumerate(list(cols) + [False]):
        if on and start is None: start = i
        if not on and start is not None:
            clusters.append((start, i - 1)); start = None
    merged = []
    for a, b in clusters:                       # glyph strokes closer than 8px belong to one icon
        if merged and a - merged[-1][1] < 8: merged[-1] = (merged[-1][0], b)
        else: merged.append((a, b))
    icons = [640 + (a + b) // 2 for a, b in merged if 8 <= b - a <= 30]
    say(f"header glyph clusters at x={icons}")
    click_x, click_y = icons[1], cy + 35
    say(f"card for beat {beat} at {card['r']}; clicking its 'Dùng MP4' icon at ({click_x},{click_y})")
    s.shot(flet, f"pick_{tag}_01_before_click.png")
    known = {id(p) for p in s.ctx.pages}
    flet.mouse.click(click_x, click_y)
    mp = None
    t0 = time.time()
    while time.time() - t0 < 240 and mp is None:       # the new tab is reported once its (slow: live search) navigation commits
        for p in s.ctx.pages:
            if id(p) not in known and "/moments_review" in p.url:
                mp = p
        flet.wait_for_timeout(500)          # pumps Playwright's events (time.sleep would not)
    assert mp is not None, "no new moments_review tab opened"
    mp.set_viewport_size({"width": 1100, "height": 1500})
    say(f"new tab opened: {mp.url}")
    assert "/moments_review" in mp.url and f"beat={beat}" in mp.url.replace("%3A", ":"), mp.url
    # the shortlist is a real You.com + yt-dlp search (8 parallel workers): give it time
    t0 = time.time()
    mp.wait_for_load_state("load", timeout=240000)
    say(f"moments page loaded in {time.time() - t0:.1f}s")
    cards = mp.locator(".card").count()
    say(f"candidate cards: {cards}; beat duration shown: {mp.locator('#beat-dur').inner_text()}")
    mp.screenshot(path=str(EVID / f"pick_{tag}_02_moments_page.png"), full_page=False)
    # wait for the YT iframe API to build players
    mp.wait_for_function("() => Object.keys(window.players || {}).length > 0", timeout=60000)
    vids = mp.evaluate("() => Object.keys(window.players)")
    say(f"YT.Player instances: {vids}")
    if isinstance(cand_pick, str):
        vid = cand_pick
    elif cand_pick == -1:        # the shortest video (quick to fetch on this slow link) that is long enough to hold a start + window
        mp.wait_for_timeout(8000)
        durs = mp.evaluate("() => Object.fromEntries(Object.entries(window.players).map(([v, p]) => [v, (p.getDuration ? p.getDuration() : 0)]))")
        say(f"player durations: {durs}")
        usable = {v: d for v, d in durs.items() if d and d > 40}
        vid = min(usable, key=usable.get)
    else:
        vid = vids[min(cand_pick, len(vids) - 1)]
    # pick the first suggested moment button of that card, like a user would
    sug = mp.locator(f"#card_{vid} .time-btn")
    start_hint = None
    if sug.count():
        label = sug.first.inner_text()
        say(f"clicking suggestion: {label}")
        sug.first.click()
        time.sleep(3.5)                                   # let the player seek + play
    cur = mp.evaluate(f"() => window.players['{vid}'].getCurrentTime()")
    state = mp.evaluate(f"() => window.players['{vid}'].getPlayerState()")
    say(f"player {vid}: getCurrentTime()={cur:.2f}s state={state} (this is what 'Dùng từ đây' will send)")
    mp.screenshot(path=str(EVID / f"pick_{tag}_03_player_seeked.png"))
    # intercept the POST so we can record exactly what was sent
    sent = {}
    mp.on("request", lambda r: sent.update(body=r.post_data, url=r.url) if r.url.endswith("/api/pick_moment") else None)
    mp.locator(f"#card_{vid} .pick-btn").click()
    say("clicked 'Dùng từ đây'")
    time.sleep(1.0)
    say(f"POST /api/pick_moment body: {sent.get('body')}")
    # follow the job through the page's own status box until ready/error
    t0 = time.time(); last = None
    while time.time() - t0 < 900:
        msg = mp.locator("#pick-msg").inner_text() if mp.locator("#pick-msg").count() else ""
        if msg != last:
            say(f"pick status: {msg}"); last = msg
        if msg.startswith("✓") or "Lỗi" in msg or "mốc sớm hơn" in msg or "quá thời gian" in msg:
            break
        time.sleep(2)
    time.sleep(2)
    mp.screenshot(path=str(EVID / f"pick_{tag}_04_done.png"))
    pv = mp.evaluate("() => { const v = document.getElementById('pick-preview'); return v ? [v.style.display, v.src, v.readyState, v.videoWidth, v.videoHeight, v.duration] : null }")
    say(f"preview element: {pv}")
    mp.close()
    time.sleep(2.5)
    s.shot(flet, f"pick_{tag}_05_review_gate_after.png")
    card = find_card(s, flet)
    say(f"review gate card after the pick: {card['label'].splitlines() if card else None}")
