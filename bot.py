import os, sys, time, re
from datetime import datetime
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

BASE_URL   = os.getenv("BASE_URL", "https://291.gospec.net:8091/login.html")
USERNAME   = os.getenv("BOT_USER")
PASSWORD   = os.getenv("BOT_PASS")
HEADLESS   = os.getenv("HEADLESS", "1") == "1"
TIMEOUT    = int(os.getenv("TIMEOUT_MS", "35000"))
SKIP_IF_NOT_MADRID_HOURS = os.getenv("RUN_GUARD", "1") == "1"
MADRID_HOURS = {9, 13, 14, 18}  # 24h

def log(msg): print(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}", flush=True)

def guard_for_timezone():
    if not SKIP_IF_NOT_MADRID_HOURS: 
        return
    try:
        import pytz
        from datetime import datetime as dt
        now_madrid = dt.now(pytz.timezone("Europe/Madrid"))
        if now_madrid.minute != 0 or now_madrid.hour not in MADRID_HOURS:
            log(f"Skipping run (Europe/Madrid {now_madrid.strftime('%H:%M')})")
            sys.exit(0)
    except Exception as e:
        log(f"Timezone guard failed ({e}), continuing anyway.")

def goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

def login(page):
    log("Opening login page…")
    goto(page, BASE_URL)

    # If already logged in, the app may redirect straight into the portal:
    if not re.search(r"login", page.url, re.I):
        log("Looks already authenticated.")
        return

    log("Filling credentials…")
    page.get_by_label(re.compile(r"^User$", re.I)).fill(USERNAME)
    page.get_by_label(re.compile(r"^Password$", re.I)).fill(PASSWORD)
    page.get_by_role("button", name=re.compile(r"^Login$", re.I)).click()
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

def go_to_remote_clocking(page):
    # Direct route after login (seen in your screenshot)
    origin = BASE_URL.split("/login")[0].rstrip("/")
    remote_url = origin + "/portal/#/remoteMark"
    log("Navigating to Remote clocking…")
    goto(page, remote_url)

    # Fallback: click the menu if needed
    if "remoteMark" not in page.url:
        log("Fallback: clicking left menu → Remote clocking")
        page.get_by_role("link", name=re.compile(r"^Remote clocking$", re.I)).click()
        page.wait_for_load_state("networkidle", timeout=TIMEOUT)

def pick_standard_time_worked(page):
    log("Opening 'Schedule' dropdown…")
    # Open the combobox (several strategies; one will stick even after minor UI changes)
    opened = False
    for locator in [
        page.get_by_role("combobox"),
        page.locator("text=Select an item").first,
        page.locator("div[role='combobox']").first,
        page.locator("div:has-text('Schedule')").locator("div:has-text('Select an item')").first,
    ]:
        try:
            locator.click(timeout=3000)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        raise RuntimeError("Could not open the Schedule dropdown.")

    log("Selecting 'Standard Time Worked'…")
    # Try role=option first, then text match in the popup list
    selected = False
    for opt in [
        page.get_by_role("option", name=re.compile(r"Standard Time Worked", re.I)),
        page.get_by_text(re.compile(r"^\s*Standard Time Worked\s*$", re.I)),
        page.locator("li,div,span").filter(has_text=re.compile(r"Standard Time Worked", re.I)).first,
    ]:
        try:
            opt.click(timeout=4000)
            selected = True
            break
        except Exception:
            continue
    if not selected:
        raise RuntimeError("Could not select 'Standard Time Worked'.")

def try_confirm(page):
    # Some portals require a final confirmation/mark/save. Try common button names safely.
    for name in [
        r"^Confirm$", r"^Save$", r"^OK$", r"^Register$", r"^Mark$", r"^Clock(ing)?$",
        r"^Accept$", r"^Submit$"
    ]:
        try:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if btn.is_visible(timeout=1000) and btn.is_enabled():
                log(f"Clicking confirmation button: {name.strip('^$')}")
                btn.click(timeout=3000)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT)
                return
        except Exception:
            continue
    log("No obvious confirmation button found; assuming selection is enough.")

def main():
    if not USERNAME or not PASSWORD:
        print("Missing BOT_USER/BOT_PASS env vars.", file=sys.stderr)
        sys.exit(2)

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
        except (PWTimeout, Exception) as e:
            shot = f"error_{int(time.time())}.png"
            try:
                page.screenshot(path=shot, full_page=True)
                log(f"Saved error screenshot: {shot}")
            except Exception:
                pass
            log(f"ERROR: {e}")
            context.close()
            sys.exit(1)
        context.close()

if __name__ == "__main__":
    main()
