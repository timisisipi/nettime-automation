import os, sys, time, re
from datetime import datetime
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL = os.getenv("BASE_URL", "https://291.gospec.net:8091/login.html")
USERNAME = os.getenv("BOT_USER")
PASSWORD = os.getenv("BOT_PASS")
HEADLESS = os.getenv("HEADLESS", "1") == "1"
TIMEOUT  = int(os.getenv("TIMEOUT_MS", "45000"))
RUN_GUARD = os.getenv("RUN_GUARD", "1") == "1"   # 1 = only Madrid hours; 0 = run now

MADRID_HOURS = {9, 13, 14, 18}  # 24h, scheduled runs

def log(msg): print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

def guard_for_timezone():
    if not RUN_GUARD:
        log("RUN_GUARD=0 → bypassing Madrid-hour check.")
        return
    try:
        import pytz
        from datetime import datetime as dt
        now_madrid = dt.now(pytz.timezone("Europe/Madrid"))
        if now_madrid.minute != 0 or now_madrid.hour not in MADRID_HOURS:
            log(f"Skipping (Europe/Madrid {now_madrid.strftime('%H:%M')}).")
            save_screenshot = os.getenv("ALWAYS_SHOT", "1") == "1"
            if save_screenshot:
                raise SystemExitWithScreenshot(0, "guard_skip")
            sys.exit(0)
    except Exception as e:
        log(f"Timezone guard failed ({e}); continuing.")

class SystemExitWithScreenshot(Exception):
    def __init__(self, code, label="exit"):
        self.code = code
        self.label = label

def goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

def find_login_frame(page):
    """Some netTime setups put login inputs inside a child frame. Try page first, then frames."""
    candidates = [page] + page.frames
    for c in candidates:
        try:
            if c.locator("input[type='password']").first.is_visible(timeout=1000):
                return c
        except Exception:
            continue
    return page

def fill_first_that_works(ctx, locators, value, what):
    for make in locators:
        try:
            el = make(ctx)
            el.wait_for(state="visible", timeout=3000)
            el.fill(value, timeout=3000)
            log(f"Filled {what} via {el}")
            return
        except Exception:
            continue
    raise RuntimeError(f"Could not locate {what} field")

def click_first_that_works(ctx, candidates, what):
    for make in candidates:
        try:
            el = make(ctx)
            el.click(timeout=3000)
            log(f"Clicked {what} via {el}")
            return
        except Exception:
            continue
    raise RuntimeError(f"Could not click {what}")

def login(page):
    log("Opening login page…")
    goto(page, BASE_URL)
    # If the app already redirected into the portal, continue
    if not re.search(r"login", page.url, re.I):
        log(f"Already past login (URL: {page.url}).")
        return

    # Work inside page or frame
    ctx = find_login_frame(page)

    log("Filling credentials (ES/EN + placeholder/name fallbacks)…")

    fill_first_that_works(ctx, [
        lambda c: c.get_by_label(re.compile(r"^(Usuario|User)$", re.I)),
        lambda c: c.locator("input[placeholder*='Usuario' i], input[placeholder*='User' i]").first,
        lambda c: c.locator("input[name='username'], input[name='user'], #username, #user").first,
        lambda c: c.locator("input[type='text'], input[type='email']").first,
    ], USERNAME, "username")

    fill_first_that_works(ctx, [
        lambda c: c.get_by_label(re.compile(r"^(Contraseña|Password)$", re.I)),
        lambda c: c.locator("input[placeholder*='Contraseña' i], input[placeholder*='Password' i]").first,
        lambda c: c.locator("input[name='password'], #password").first,
        lambda c: c.locator("input[type='password']").first,
    ], PASSWORD, "password")

    click_first_that_works(ctx, [
        lambda c: c.get_by_role("button", name=re.compile(r"^Login$", re.I)),
        lambda c: c.get_by_text(re.compile(r"^\s*Login\s*$", re.I)),
        lambda c: c.locator("button[type='submit']").first,
        lambda c: c.locator("input[type='submit']").first,
        lambda c: c.locator("button").filter(has_text=re.compile(r"login", re.I)).first,
    ], "Login")

    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    if re.search(r"login", page.url, re.I):
        # Some builds show an inline error; screenshot will reveal it
        raise RuntimeError("Still on login page after submitting credentials.")

def go_to_remote_clocking(page):
    u = urlparse(BASE_URL)
    base = f"{u.scheme}://{u.hostname}" + (f":{u.port}" if u.port else "")
    remote_url = base + "/portal/#/remoteMark"
    log("Navigating to Remote clocking…")
    goto(page, remote_url)

    if "remoteMark" not in page.url:
        log("Fallback: clicking left menu → (Marcaje remoto / Remote clocking)")
        try:
            page.get_by_role("link", name=re.compile(r"^(Marcaje remoto|Remote clocking)$", re.I)).click(timeout=4000)
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
        except Exception:
            pass

def pick_standard_time_worked(page):
    log("Opening 'Schedule' dropdown…")
    opened = False
    for f in [
        lambda: page.get_by_role("combobox").first,
        lambda: page.locator("div[role='combobox']").first,
        lambda: page.get_by_text(re.compile(r"^Select an item|\bSeleccionar un elemento\b|Seleccionar un ítem", re.I)).first,
        lambda: page.locator("div:has-text('Schedule'), div:has-text('Horario')").locator("div").first,
    ]:
        try:
            f().click(timeout=3000)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        raise RuntimeError("Could not open the Schedule dropdown.")

    log("Selecting 'Standard Time Worked'…")
    selected = False
    for f in [
        lambda: page.get_by_role("option", name=re.compile(r"Standard Time Worked", re.I)),
        lambda: page.get_by_text(re.compile(r"^\s*Standard Time Worked\s*$", re.I)).first,
        # Spanish instances sometimes list just a single 'Trabajo estándar' – leave English first per your screenshot
        lambda: page.get_by_text(re.compile(r"Trabajo estándar|Tiempo estándar", re.I)).first,
        lambda: page.locator("li,div,span").filter(has_text=re.compile(r"Standard Time Worked|Trabajo estándar|Tiempo estándar", re.I)).first,
    ]:
        try:
            f().click(timeout=3000)
            selected = True
            break
        except Exception:
            continue
    if not selected:
        raise RuntimeError("Could not select 'Standard Time Worked'.")

def try_confirm(page):
    # Many setups auto-apply on selection; others need an explicit confirm/mark.
    for name in [r"^Confirm(ar)?$", r"^Save$", r"^OK$", r"^Register$", r"^Mark(ing)?$", r"^Aceptar$",
                 r"^Submit$", r"^Guardar$"]:
        try:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if btn.is_visible(timeout=1000) and btn.is_enabled():
                log(f"Clicking confirmation button: {name.strip('^$')}")
                btn.click(timeout=3000)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT)
                break
        except Exception:
            continue

def main():
    if not USERNAME or not PASSWORD:
        print("Missing BOT_USER/BOT_PASS env vars.", file=sys.stderr)
        raise SystemExitWithScreenshot(2, "missing_env")

    guard_for_timezone()

    with sync_playwright() as p:
        context = p.chromium.launch(headless=HEADLESS, args=[
            "--no-sandbox", "--disable-dev-shm-usage", "--ignore-certificate-errors"
        ])
        page = context.new_page()
        try:
            login(page)
            go_to_remote_clocking(page)
            pick_standard_time_worked(page)
            try_confirm(page)
            shot = f"success_{int(time.time())}.png"
            page.screenshot(path=shot, full_page=True)
            log(f"Done. Screenshot: {shot}")
        except SystemExitWithScreenshot as e:
            shot = f"{e.label}_{int(time.time())}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                log(f"Saved {e.label} screenshot: {shot}")
            except Exception:
                log("Could not save screenshot on controlled exit.")
            context.close()
            sys.exit(e.code)
        except Exception as e:
            shot = f"error_{int(time.time())}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                log(f"Saved error screenshot: {shot}")
            except Exception:
                log("Could not save error screenshot.")
            log(f"ERROR: {e}")
            context.close()
            sys.exit(1)
        context.close()

if __name__ == "__main__":
    main()
