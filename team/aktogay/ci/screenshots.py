#!/usr/bin/env python3
"""Screenshots of the Stand for the team's CI run: scene, what-if, twin card, Grafana."""

import json
import os
import sys
import time
import urllib.request

from playwright.sync_api import sync_playwright

OUT = sys.argv[1] if len(sys.argv) > 1 else "shots"
os.makedirs(OUT, exist_ok=True)
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
ENV = dict(line.strip().split("=", 1) for line in open(os.path.join(ROOT, "deploy/compose/.env"))
           if "=" in line and not line.startswith("#"))
UI = "http://localhost:8070"

with sync_playwright() as p:
    browser = p.chromium.launch(args=["--use-gl=swiftshader", "--enable-webgl", "--ignore-gpu-blocklist"])
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    console = []
    page.on("console", lambda m: console.append(f"{m.type}: {m.text}"))
    page.on("pageerror", lambda e: console.append(f"pageerror: {e}"))
    page.goto(UI)
    page.wait_for_function("window.__aktogayReady === true", timeout=120000)
    time.sleep(20)
    page.screenshot(path=f"{OUT}/1-scene.png")

    page.click("button.chip:has-text(\"Съезд Восточный, низ\")")
    page.click("button.chip:text-is('EX-01')")
    time.sleep(45)
    page.screenshot(path=f"{OUT}/2-whatif.png")

    page.click("button.chip:text-is('EX-01')")
    page.click("button.chip:has-text(\"Съезд Восточный, низ\")")
    page.mouse.click(800, 450)
    time.sleep(5)
    page.screenshot(path=f"{OUT}/3-after-undo.png")
    page.evaluate("document.querySelector('#twin-card').scrollIntoView()")
    page.screenshot(path=f"{OUT}/4-panel.png", full_page=True)

    g = browser.new_page(viewport={"width": 1600, "height": 1000})
    g.goto(f"http://localhost:{ENV['GRAFANA_PORT']}/login")
    g.fill("input[name=user]", "admin")
    g.fill("input[name=password]", ENV["GRAFANA_ADMIN_PASSWORD"])
    g.click("button[type=submit]")
    g.wait_for_load_state("networkidle")
    g.goto(f"http://localhost:{ENV['GRAFANA_PORT']}/d/aktogay-haulage?orgId=1&from=now-15m&to=now&kiosk")
    time.sleep(15)
    g.screenshot(path=f"{OUT}/5-grafana.png", full_page=True)

    with open(f"{OUT}/console.txt", "w") as fh:
        fh.write("\n".join(console))
    browser.close()
