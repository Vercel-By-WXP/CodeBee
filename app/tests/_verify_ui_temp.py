from playwright.sync_api import sync_playwright

MAIN_PAGES = {"overview", "runs", "automation", "browser"}
SETTINGS = [
    "usage", "agents", "models", "bindings", "skills", "knowledge",
    "market", "orch", "data", "appearance", "about",
]
PAGES = ["overview", "runs", "automation", "browser"] + SETTINGS


def dismiss_overlays(page):
    page.evaluate("""() => {
      if (window.welcomeClose) window.welcomeClose();
      const ask = document.querySelector('#ask');
      if (ask) ask.classList.add('hidden');
    }""")


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    for width, height in ((1440, 900), (390, 844)):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto("http://127.0.0.1:8877/", wait_until="domcontentloaded")
        page.wait_for_timeout(900)
        dismiss_overlays(page)
        for name in PAGES:
            shell = "main" if name in MAIN_PAGES else "settings"
            page.evaluate("([name, shell]) => window.switchTab(name, shell)", [name, shell])
            page.wait_for_timeout(120)
            dismiss_overlays(page)
            result = page.evaluate("""() => {
              const active = [...document.querySelectorAll('#page-settings > .subpage')]
                .find(x => !x.classList.contains('hidden'));
              const rect = active.getBoundingClientRect();
              const rail = [...document.querySelectorAll('.side-rail .rail-btn')]
                .find(x => x.classList.contains('active'));
              return {
                active: active.id,
                x: Math.round(rect.x), width: Math.round(rect.width),
                scrollWidth: document.documentElement.scrollWidth,
                clientWidth: document.documentElement.clientWidth,
                rail: rail && (rail.dataset.railPage || rail.id),
              };
            }""")
            if result["active"] != "sub-" + name:
                raise AssertionError((width, name, "wrong page", result))
            if result["scrollWidth"] > result["clientWidth"] + 1:
                raise AssertionError((width, name, "horizontal overflow", result))
            expected_rail = name if name in MAIN_PAGES else "btn-rail-settings"
            if result["rail"] != expected_rail:
                raise AssertionError((width, name, "wrong rail", result))
            print(width, name, result)

        page.evaluate("""() => {
          document.body.classList.add('inspector-open');
          document.querySelector('#inspector').classList.remove('hidden');
        }""")
        inspector = page.evaluate("""() => {
          const app = document.querySelector('#app');
          const panel = document.querySelector('#inspector');
          const s = getComputedStyle(app);
          const r = panel.getBoundingClientRect();
          return {columns: s.gridTemplateColumns, position: s.position, x: Math.round(r.x), width: Math.round(r.width)};
        }""")
        if width > 900 and inspector["position"] != "static":
            raise AssertionError((width, "desktop inspector", inspector))
        if width <= 900 and inspector["position"] != "fixed":
            raise AssertionError((width, "mobile inspector", inspector))
        print(width, "inspector", inspector)
        page.close()
    browser.close()
