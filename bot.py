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

    if not re.search(r"login", page.url, re.I):
        log(f"Already past login (URL: {page.url}).")
        return

    ctx = find_login_frame(page)

    log("Filling credentials…")
    fill_first_that_works(ctx, [
        lambda c: c.get_by_label(re.compile(r"^(Usuario|User)$", re.I)),
        lambda c: c.locator("input[placeholder*='Usuario' i], input[placeholder*='User' i]").first,
        lambda c: c.locator("input[name='username'], input[name='user'], #username, #user").first,
        lambda c: c.locator("input[type='text'], input[type='email']").first,
    ], USERNAME, "username")

    pwd = [
        lambda c: c.get_by_label(re.compile(r"^(Contraseña|Password)$", re.I)),
        lambda c: c.locator("input[placeholder*='Contraseña' i], input[placeholder*='Password' i]").first,
        lambda c: c.locator("input[name='password'], #password").first,
        lambda c: c.locator("input[type='password']").first,
    ]
    fill_first_that_works(ctx, pwd, PASSWORD, "password")

    # Log console events to the action logs (helps diagnose IP blocks / JS errors)
    page.on("console", lambda m: print(f"[console] {m.type}: {m.text}", flush=True))

    # Locate the login button once
    btn_candidates = [
        lambda c: c.get_by_role("button", name=re.compile(r"^Login$", re.I)),
        lambda c: c.get_by_text(re.compile(r"^\s*Login\s*$", re.I)),
        lambda c: c.locator("button[type='submit']").first,
        lambda c: c.locator("input[type='submit']").first,
        lambda c: c.locator("button").filter(has_text=re.compile(r"login", re.I)).first,
    ]
    button = None
    for make in btn_candidates:
        try:
            b = make(ctx)
            b.wait_for(state="visible", timeout=3000)
            button = b
            break
        except Exception:
            continue
    if not button:
        raise RuntimeError("Login button not found")

    # 1) Normal click (with scroll into view). If blocked, force click.
    try:
        button.scroll_into_view_if_needed(timeout=2000)
        try:
            button.click(timeout=3000)
            log("Clicked Login (normal).")
        except Exception:
            button.click(timeout=3000, force=True)
            log("Clicked Login (force).")
    except Exception as e:
        log(f"Login button click failed: {e}")

    # Wait for either success OR an error/remaining-on-page
    page.wait_for_timeout(1500)  # give spinner a moment
    if re.search(r"login", page.url, re.I):
        log("Still on login after normal click. Trying ENTER on password field…")
        # 2) Press Enter in password field
        try:
            # Re-find password field and press Enter
            pass_el = None
            for make in pwd:
                try:
                    pass_el = make(ctx); pass_el.wait_for(state="visible", timeout=1000); break
                except Exception: continue
            if pass_el:
                pass_el.press("Enter", timeout=2000)
                page.wait_for_timeout(1500)
        except Exception as e:
            log(f"Enter submit failed: {e}")

    if re.search(r"login", page.url, re.I):
        log("Still on login after ENTER. Trying JS click on form submit…")
        # 3) JS dispatch click/submit
        try:
            ctx.evaluate("""
                () => {
                  const btn = document.querySelector("button[type='submit'],input[type='submit']");
                  if (btn) { btn.click(); }
                  const frm = btn ? btn.closest('form') : document.querySelector('form');
                  if (frm) { frm.dispatchEvent(new Event('submit', {bubbles:true,cancelable:true})); }
                }
            """)
            page.wait_for_timeout(1500)
        except Exception as e:
            log(f"JS submit failed: {e}")

    # Final wait and verdict
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    log(f"Post-submit URL: {page.url}")

    # If still on login, try to detect visible error text
    if re.search(r"login", page.url, re.I):
        try:
            err = ctx.locator("*, .error, .alert, .validation").filter(
                has_text=re.compile(r"(error|incorrect|inválid|usuario|contraseñ|denegad|permitid)", re.I)
            ).first
            if err and err.is_visible(timeout=1000):
                log(f"Login message: {err.inner_text()}")
        except Exception:
            pass
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

def pick_sin_incidencia(page):
    log("Opening 'Horario' dropdown…")
    # Open the combobox
    opened = False
    for f in [
        lambda: page.get_by_role("combobox").first,
        lambda: page.locator("div[role='combobox']").first,
        lambda: page.get_by_text(re.compile(r"^Seleccione un elemento|Select an item", re.I)).first,
        lambda: page.locator("div:has-text('Horario')").locator("div").first,
    ]:
        try:
            f().click(timeout=3000)
            opened = True
            break
        except Exception:
            continue
    if not opened:
        raise RuntimeError("No pude abrir el desplegable de Horario.")

    log("Selecting 'Sin incidencia'…")
    # Try direct hit first
    targets = [
        lambda: page.get_by_role("option", name=re.compile(r"^\s*Sin incidencia\s*$", re.I)),
        lambda: page.get_by_text(re.compile(r"^\s*Sin incidencia\s*$", re.I)).first,
        lambda: page.locator("li,div,span").filter(has_text=re.compile(r"^\s*Sin incidencia\s*$", re.I)).first,
    ]
    selected = False
    for t in targets:
        try:
            t().click(timeout=2000)
            selected = True
            break
        except Exception:
            continue

    # If not visible on page 1, use the built-in search or go to page 2
    if not selected:
        log("Not on first page; trying the search box…")
        try:
            search = page.locator("input[type='text']").filter(
                has_not=page.locator("input[type='password']")
            ).nth(0)
            search.fill("Sin incidencia", timeout=2000)
            page.wait_for_timeout(500)
            page.get_by_text(re.compile(r"^\s*Sin incidencia\s*$", re.I)).first.click(timeout=3000)
            selected = True
        except Exception:
            pass

    if not selected:
        log("Trying pagination → page 2…")
        try:
            page.get_by_text(re.compile(r"^\s*2\s*$")).click(timeout=2000)
            page.get_by_text(re.compile(r"^\s*Sin incidencia\s*$", re.I)).first.click(timeout=3000)
            selected = True
        except Exception:
            pass

    if not selected:
        raise RuntimeError("No pude seleccionar 'Sin incidencia'.")

def try_confirm(page):
    # Spanish/English variants commonly used
    for name in [
        r"^Guardar$", r"^Aceptar$", r"^Confirmar$",
        r"^Save$", r"^OK$", r"^Confirm$",
        r"^Registrar$", r"^Marcar$", r"^Clock(ing)?$"
    ]:
        try:
            btn = page.get_by_role("button", name=re.compile(name, re.I))
            if btn.is_visible(timeout=1000) and btn.is_enabled():
                log(f"Clicking confirmation button: {name.strip('^$')}")
                btn.scroll_into_view_if_needed(timeout=1000)
                try:
                    btn.click(timeout=2000)
                except Exception:
                    btn.click(timeout=2000, force=True)
                page.wait_for_load_state("networkidle", timeout=TIMEOUT)
                # optional: wait a breath for any toast
                page.wait_for_timeout(800)
                return
        except Exception:
            continue
    log("No visible 'Guardar/Save' button found; assuming selection auto-applied.")

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
            pick_sin_incidencia(page)
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
