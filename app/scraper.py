"""Login ad Amazon Moment e lettura delle settimane di turni.

Uso da riga di comando (prima prova):
    python -m app.scraper
"""
from __future__ import annotations

import json
import os
import pathlib
import sys
import time
from datetime import datetime

from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .config import Config

EXTRACT_JS = (pathlib.Path(__file__).parent / "extract.js").read_text(encoding="utf-8")

USER_FIELDS = [
    "input[type=email]",
    "input[autocomplete=username]",
    "input[name*=user i]", "input[id*=user i]",
    "input[name*=login i]", "input[id*=login i]",
    "input[name*=alias i]", "input[id*=alias i]",
    "input[type=text]",
]
SUBMIT_BUTTONS = [
    "button[type=submit]", "input[type=submit]",
    "button:has-text('Sign in')", "button:has-text('Log in')", "button:has-text('Login')",
    "button:has-text('Accedi')", "button:has-text('Continue')", "button:has-text('Next')",
]
CALENDAR = "#calendar .fc-view"


class LoginError(Exception):
    pass


class PortalError(Exception):
    pass


def _first_visible(page, selectors):
    for sel in selectors:
        if not sel:
            continue
        loc = page.locator(sel)
        try:
            for i in range(min(loc.count(), 5)):
                el = loc.nth(i)
                if el.is_visible():
                    return el
        except Exception:
            continue
    return None


def _has_calendar(page) -> bool:
    try:
        return page.locator(CALENDAR).count() > 0
    except Exception:
        return False


def _login(page, cfg: Config):
    """Compila il modulo di login (anche se diviso in due passaggi: utente, poi password)."""
    if not cfg.username or not cfg.password:
        raise LoginError("PORTAL_USERNAME e PORTAL_PASSWORD non sono impostati nel file .env")

    for _step in range(4):
        if _has_calendar(page):
            return
        pw = _first_visible(page, [cfg.pass_selector, "input[type=password]"])
        user = _first_visible(page, [cfg.user_selector] + USER_FIELDS)
        if not pw and not user:
            # Magari la pagina sta ancora caricando o reindirizzando.
            try:
                page.wait_for_selector(f"{CALENDAR}, input[type=password], input[type=text], input[type=email]",
                                       timeout=20000)
            except PWTimeout:
                raise LoginError("Non trovo né il calendario né un modulo di login")
            continue

        if user:
            try:
                if not user.input_value():
                    user.fill(cfg.username)
            except Exception:
                pass  # campo in sola lettura (es. utente già indicato al passaggio prima)
        if pw:
            pw.fill(cfg.password)

        button = _first_visible(page, [cfg.submit_selector] + SUBMIT_BUTTONS)
        if button:
            button.click()
        else:
            (pw or user).press("Enter")

        try:
            page.wait_for_load_state("domcontentloaded", timeout=30000)
            page.wait_for_selector(f"{CALENDAR}, input[type=password]", timeout=30000)
        except PWTimeout:
            pass

        if pw and not _has_calendar(page) and _first_visible(page, ["input[type=password]"]):
            # La password è stata inviata ma il modulo è ancora lì: credenziali rifiutate.
            raise LoginError("Il portale ha rifiutato username o password (o chiede un passaggio in più)")

    if not _has_calendar(page):
        raise LoginError("Dopo il login non arrivo alla pagina dei turni")


def _first_day(page) -> str:
    return page.eval_on_selector("#calendar th.fc-day-header[data-date]", "el => el.getAttribute('data-date')")


def _settle(page):
    try:
        page.wait_for_load_state("networkidle", timeout=15000)
    except PWTimeout:
        pass
    time.sleep(1.0)


def _go(page, label: str):
    before = _first_day(page)
    page.locator("week-selector button", has_text=label).first.click()
    if label != "Now":
        page.wait_for_function(
            "d => { const el = document.querySelector('#calendar th.fc-day-header[data-date]');"
            " return el && el.getAttribute('data-date') !== d; }",
            arg=before, timeout=20000)
    _settle(page)


def _read(page) -> dict:
    page.wait_for_selector(CALENDAR, timeout=30000)
    return page.evaluate(EXTRACT_JS)


def _dump_debug(page, cfg: Config, reason: str):
    """Salva screenshot e HTML della pagina per capire cosa non va (senza password)."""
    try:
        d = pathlib.Path(cfg.data_dir) / "debug"
        d.mkdir(parents=True, exist_ok=True)
        page.evaluate("() => document.querySelectorAll('input[type=password]').forEach(i => i.value = '')")
        page.screenshot(path=str(d / "ultima_pagina.png"), full_page=True)
        (d / "ultima_pagina.html").write_text(page.content(), encoding="utf-8")
        (d / "motivo.txt").write_text(f"{datetime.now().isoformat()}\n{reason}\nURL: {page.url}\n", encoding="utf-8")
    except Exception:
        pass


def fetch_weeks(cfg: Config = Config) -> tuple[list[dict], str]:
    """Restituisce (settimane, metodo). Ogni settimana: {'week': [date...], 'segments': [...]}."""
    state_path = os.path.join(cfg.data_dir, "sessione_browser.json")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=cfg.headless, args=["--no-sandbox", "--disable-dev-shm-usage"])
        ctx_args = dict(viewport={"width": 1280, "height": 1600}, timezone_id=cfg.tz, locale="en-US")
        if os.path.exists(state_path):
            ctx_args["storage_state"] = state_path
        ctx = browser.new_context(**ctx_args)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        try:
            page.goto(cfg.portal_url, wait_until="domcontentloaded", timeout=60000)
            _settle(page)
            if not _has_calendar(page):
                _login(page, cfg)
            # Se il login riporta altrove, torna alla pagina dei turni.
            if not _has_calendar(page):
                page.goto(cfg.portal_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_selector(CALENDAR, timeout=45000)
            _settle(page)

            weeks, methods = [], set()

            def grab():
                r = _read(page)
                if not r.get("week"):
                    raise PortalError("La pagina dei turni non mostra la settimana")
                weeks.append({"week": r["week"], "segments": r["segments"]})
                if r.get("method"):
                    methods.add(r["method"])

            _go(page, "Now")
            grab()
            for _ in range(cfg.weeks_ahead):
                _go(page, "Next")
                grab()
            if cfg.weeks_back:
                _go(page, "Now")
                for _ in range(cfg.weeks_back):
                    _go(page, "Prev")
                    grab()

            os.makedirs(cfg.data_dir, exist_ok=True)
            ctx.storage_state(path=state_path)
            return weeks, "+".join(sorted(methods)) or "?"
        except Exception as e:
            _dump_debug(page, cfg, f"{type(e).__name__}: {e}")
            if isinstance(e, PWTimeout):
                raise PortalError(f"Il portale non ha risposto in tempo ({e.__class__.__name__})") from e
            raise
        finally:
            ctx.close()
            browser.close()


if __name__ == "__main__":
    from .model import build_shifts, normalize

    try:
        weeks, method = fetch_weeks(Config)
    except Exception as e:
        print(f"ERRORE: {e}\nGuarda i file in {Config.data_dir}/debug/", file=sys.stderr)
        sys.exit(1)
    print(f"Lette {len(weeks)} settimane (metodo: {method})")
    segs = [s for w in weeks for s in normalize(w["segments"])]
    for sh in build_shifts(segs):
        print(f'{sh["date"]}  {sh["start"][11:]}–{sh["end"][11:]}  ({sh["minutes_worked"] / 60:g} h)')
    if "--json" in sys.argv:
        print(json.dumps(weeks, indent=1))
