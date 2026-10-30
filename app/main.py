"""Server dei turni: aggiornamento periodico dal portale, API per la PWA e feed calendario."""
from __future__ import annotations

import contextlib
import hmac
import json
import os
import pathlib
import threading
import time
import traceback
import urllib.request
from datetime import date, datetime, timedelta

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .config import Config
from .ics import build_ics
from .model import Store
from .scraper import LoginError, fetch_weeks

STATIC = pathlib.Path(__file__).parent / "static"
store = Store(os.path.join(Config.data_dir, "turni.json"))
TOKEN = Config.ensure_token()

_run_lock = threading.Lock()
_state = {"running": False, "paused_until": None, "last_manual": 0.0}


def log(msg: str):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- aggiornamento

def notify(changes: list[dict]):
    if not Config.notify_webhook or not changes:
        return
    giorni = ["lun", "mar", "mer", "gio", "ven", "sab", "dom"]
    righe = []
    for c in changes:
        d = date.fromisoformat(c["date"])
        g = f'{giorni[d.weekday()]} {d.day}/{d.month}'
        if c["type"] == "changed":
            righe.append(f'{g}: {c["before"]} → {c["after"]}')
        elif c["type"] == "added":
            righe.append(f'{g}: nuovo turno {c["after"]}')
        else:
            righe.append(f'{g}: turno {c["before"]} tolto')
    body = json.dumps({"title": "Turni cambiati", "message": "\n".join(righe), "changes": changes}).encode()
    try:
        req = urllib.request.Request(Config.notify_webhook, data=body, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=15).read()
        log("Notifica modifiche inviata")
    except Exception as e:
        log(f"Notifica non inviata: {e}")


def refresh(reason: str) -> None:
    if not _run_lock.acquire(blocking=False):
        return
    _state["running"] = True
    try:
        log(f"Lettura turni ({reason})...")
        weeks, method = fetch_weeks(Config)
        changes = store.apply_weeks(weeks, method)
        _state["paused_until"] = None
        log(f"Ok: {len(weeks)} settimane, metodo {method}, {len(changes)} modifiche")
        notify(changes)
    except LoginError as e:
        # Per non rischiare il blocco dell'account, dopo un login fallito ci si ferma per 12 ore
        # (resta possibile riprovare a mano dalla app).
        _state["paused_until"] = time.time() + 12 * 3600
        store.set_error(f"Login: {e}")
        log(f"Login fallito: {e}")
    except Exception as e:
        store.set_error(str(e) or type(e).__name__)
        log(f"Errore: {e}\n{traceback.format_exc()}")
    finally:
        _state["running"] = False
        _run_lock.release()


def _due() -> bool:
    if _state["paused_until"] and time.time() < _state["paused_until"]:
        return False
    h = datetime.now().hour
    qs, qe = Config.quiet_start, Config.quiet_end
    in_quiet = (qs > qe and (h >= qs or h < qe)) or (qs < qe and qs <= h < qe)
    last = store.data.get("last_attempt")
    if not last:
        return True
    if in_quiet:
        return False
    wait = timedelta(hours=Config.refresh_hours)
    if store.data.get("last_error"):
        wait = min(wait, timedelta(hours=1))
    return datetime.now() - datetime.fromisoformat(last) >= wait


def scheduler():
    time.sleep(5)
    while True:
        try:
            if _due():
                refresh("automatica")
        except Exception as e:
            log(f"Scheduler: {e}")
        time.sleep(60)


# ---------------------------------------------------------------- API

def authorized(request: Request) -> bool:
    tok = request.query_params.get("token") or ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
    return bool(tok) and hmac.compare_digest(tok, TOKEN)


def deny():
    return JSONResponse({"error": "token mancante o errato"}, status_code=401)


def status_payload():
    d = store.data
    return {
        "updated_at": d.get("updated_at"),
        "last_attempt": d.get("last_attempt"),
        "last_error": d.get("last_error"),
        "running": _state["running"],
        "paused": bool(_state["paused_until"] and time.time() < _state["paused_until"]),
        "method": d.get("method"),
        "configured": bool(Config.username and Config.password),
    }


async def api_data(request: Request):
    if not authorized(request):
        return deny()
    try:
        offers = [s for day in store.data["days"].values() for s in day if s["kind"] == "offer"]
        return JSONResponse({
            "today": date.today().isoformat(),
            "now": datetime.now().isoformat(timespec="minutes"),
            "shifts": store.shifts(),
            "known_dates": store.known_dates(),
            "offers": offers,
            "notes": [s for day in store.data["days"].values() for s in day if s["kind"] in ("note", "absence")],
            "changes": store.data.get("changes", [])[:10],
            "status": status_payload(),
        })
    except Exception as e:
        log(f"Errore in /api/data: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500,
                            headers={"X-Errore": type(e).__name__})


async def api_next(request: Request):
    """Prossimo turno, comodo anche per un sensore REST di Home Assistant."""
    if not authorized(request):
        return deny()
    now = datetime.now().isoformat(timespec="minutes")
    nxt = next((s for s in store.shifts() if s["end"] > now), None)
    if not nxt:
        return JSONResponse({"state": None})
    return JSONResponse({
        "state": nxt["start"], "start": nxt["start"], "end": nxt["end"],
        "in_corso": nxt["start"] <= now, "ore": round(nxt["minutes_worked"] / 60, 2),
        "note": ", ".join(nxt["highlights"]),
    })


async def api_refresh(request: Request):
    if not authorized(request):
        return deny()
    if _state["running"]:
        return JSONResponse({"ok": True, "status": status_payload()})
    if time.time() - _state["last_manual"] < 300:
        return JSONResponse({"ok": False, "error": "Aggiornamento già chiesto da poco: riprova tra qualche minuto",
                             "status": status_payload()}, status_code=429)
    _state["last_manual"] = time.time()
    threading.Thread(target=refresh, args=("manuale",), daemon=True).start()
    _state["running"] = True
    return JSONResponse({"ok": True, "status": status_payload()})


async def api_status(request: Request):
    if not authorized(request):
        return deny()
    return JSONResponse(status_payload())


async def calendar_ics(request: Request):
    if not authorized(request):
        return Response("token mancante o errato", status_code=401)
    q = request.query_params
    try:
        alarm = int(q.get("avviso", "0"))
    except ValueError:
        alarm = 0
    body = build_ics(store.shifts(), Config.tz, with_breaks=q.get("pause") == "1", alarm_min=alarm)
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="turni.ics"', "Cache-Control": "no-cache"})


async def index(request: Request):
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


async def sw(request: Request):
    return FileResponse(STATIC / "sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


async def manifest(request: Request):
    return FileResponse(STATIC / "manifest.webmanifest", media_type="application/manifest+json")


async def health(request: Request):
    return JSONResponse({"ok": True})


@contextlib.asynccontextmanager
async def lifespan(app):
    startup()
    yield


def startup():
    log(f"Turni avviato sulla porta {Config.port}. Dati in {Config.data_dir}")
    log(f"Token di accesso per app e calendario: {TOKEN}")
    if not Config.username or not Config.password:
        log("ATTENZIONE: PORTAL_USERNAME / PORTAL_PASSWORD non impostati: nessuna lettura automatica")
        return
    threading.Thread(target=scheduler, daemon=True).start()


app = Starlette(
    routes=[
        Route("/", index),
        Route("/sw.js", sw),
        Route("/manifest.webmanifest", manifest),
        Route("/healthz", health),
        Route("/api/data", api_data),
        Route("/api/next", api_next),
        Route("/api/status", api_status),
        Route("/api/refresh", api_refresh, methods=["POST"]),
        Route("/calendar.ics", calendar_ics),
        Route("/turni.ics", calendar_ics),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=Config.port, log_level="warning")
