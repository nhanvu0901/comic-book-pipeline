"""Tiny helpers over a persistent headless Chrome (CDP :9333) so the Flet session survives between steps."""
import json, re, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

EVID = Path("/tmp/p1_evidence"); EVID.mkdir(exist_ok=True)
BASE = "http://localhost:8560"


class Session:
    def __enter__(self):
        self.pw = sync_playwright().start()
        self.browser = self.pw.chromium.connect_over_cdp("http://localhost:9333")
        self.ctx = self.browser.contexts[0] if self.browser.contexts else self.browser.new_context(viewport={"width": 1400, "height": 1000})
        return self

    def __exit__(self, *a):
        self.pw.stop()          # disconnects; the browser (and its pages) stay

    def flet_page(self):
        for p in self.ctx.pages:
            if p.url.startswith(BASE) and "/moments_review" not in p.url and "/api/" not in p.url:
                return p
        return None

    def open_flet(self):
        p = self.flet_page()
        if p is None:
            p = self.ctx.new_page()
            p.set_viewport_size({"width": 1400, "height": 1000})
            p.goto(BASE + "/", wait_until="load")
        self.enable_semantics(p)
        return p

    def enable_semantics(self, p):
        if p.locator("flt-semantics").count() >= 3:
            return                                   # already enabled in this page (the placeholder is gone then)
        p.wait_for_selector("flt-semantics-placeholder, flt-semantics", state="attached", timeout=60000)
        if p.locator("flt-semantics").count() < 3:
            p.evaluate("() => { const e=document.querySelector('flt-semantics-placeholder'); if(e){e.focus(); e.click();} }")
            time.sleep(1.5)

    def labels(self, p):
        return p.evaluate("""() => Array.from(document.querySelectorAll('flt-semantics')).map(e => ({
            label: (e.getAttribute('aria-label') || '').trim(), role: e.getAttribute('role') || '',
            r: (() => { const b = e.getBoundingClientRect(); return [Math.round(b.x), Math.round(b.y), Math.round(b.width), Math.round(b.height)]; })()
        })).filter(x => x.label)""")

    def shot(self, p, name):
        path = EVID / name
        p.screenshot(path=str(path))
        return path

    def click_label(self, p, pattern, nth=0, exact=False, timeout=15):
        """Click the nth semantics node whose label matches (regex or exact string)."""
        t0 = time.time()
        while time.time() - t0 < timeout:
            items = [x for x in self.labels(p) if (x["label"] == pattern if exact else re.search(pattern, x["label"]))]
            if len(items) > nth:
                x, y, w, h = items[nth]["r"]
                p.mouse.click(x + w / 2, y + h / 2)
                return items[nth]
            time.sleep(0.5)
        raise TimeoutError(f"no semantics node matching {pattern!r} (have: {[x['label'] for x in self.labels(p)][:40]})")
