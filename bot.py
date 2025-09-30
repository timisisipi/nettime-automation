import re
from urllib.parse import urlparse

def goto(page, url):
    page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

def _fill_first_that_works(page, locators, value, what):
    for loc in locators:
        try:
            el = loc
            # If it's a callable producing a locator, call it
            if callable(loc):
                el = loc()
            el.wait_for(state="visible", timeout=3000)
            el.fill(value, timeout=3000)
            return True
        except Exception:
            continue
    raise RuntimeError(f"Could not locate {what} field")

def login(page):
    log("Opening login page…")
    goto(page, BASE_URL)

    # If already beyond login, skip
    if not re.search(r"login", page.url, re.I):
        log("Looks already authenticated.")
        return

    log("Filling credentials (handles ES/EN and missing labels)…")

    # USER field strategies
    user_locators = [
        # Proper labels (ES/EN)
        lambda: page.get_by_label(re.compile(r"^(Usuario|User)$", re.I)),
        # Placeholder attribute
        lambda: page.locator("input[placeholder*='Usuario' i],input[placeholder*='User' i]").first,
        # Common name/id attributes
        lambda: page.locator("input[name='username'],input[name='user'],#username,#user").first,
        # Fallback: first visible text-like input
        lambda: page.locator("input[type='text'],input[type='email']").first,
    ]
    _fill_first_that_works(page, user_locators, USERNAME, "username")

    # PASSWORD field strategies
    pass_locators = [
        lambda: page.get_by_label(re.compile(r"^(Contraseña|Password)$", re.I)),
        lambda: page.locator("input[placeholder*='Contraseña' i],input[placeholder*='Password' i]").first,
        lambda: page.locator("input[name='password'],#password").first,
        lambda: page.locator("input[type='password']").first,
    ]
    _fill_first_that_works(page, pass_locators, PASSWORD, "password")

    # Click Login
    clicked = False
    for btn in [
        lambda: page.get_by_role("button", name=re.compile(r"^Login$", re.I)),
        lambda: page.get_by_text(re.compile(r"^\s*Login\s*$", re.I)),
        lambda: page.locator("button[type='submit']").first,
        lambda: page.locator("input[type='submit']").first,
        lambda: page.locator("button").filter(has_text=re.compile(r"login", re.I)).first,
    ]:
        try:
            b = btn()
            b.click(timeout=3000)
            clicked = True
            break
        except Exception:
            continue
    if not clicked:
        raise RuntimeError("Could not click Login button")

    # Wait for redirect/network to settle
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # If still on login, error out
    if re.search(r"login", page.url, re.I):
        raise RuntimeError("Still on login page after submitting credentials")

def go_to_remote_clocking(page):
    # Build base like https://291.gospec.net:8091
    u = urlparse(BASE_URL)
    base = f"{u.scheme}://{u.hostname}"
    if u.port:
        base += f":{u.port}"

    remote_url = base + "/portal/#/remoteMark"
    log("Navigating to Remote clocking…")
    goto(page, remote_url)

    if "remoteMark" not in page.url:
        log("Fallback: clicking left menu → Remote clocking")
        try:
            page.get_by_role("link", name=re.compile(r"^Remote clocking$", re.I)).click(timeout=3000)
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
        except Exception:
            # Spanish UI fallback
            page.get_by_role("link", name=re.compile(r"^Marcaje remoto|Remote clocking$", re.I)).click(timeout=3000)
            page.wait_for_load_state("networkidle", timeout=TIMEOUT)
