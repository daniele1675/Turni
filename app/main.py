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
from .people import OWNER, InvalidInput, People
from .scraper import LoginError, fetch_weeks

STATIC = pathlib.Path(__file__).parent / "static"
store = Store(os.path.join(Config.data_dir, "turni.json"))
TOKEN = Config.ensure_token()
people = People(Config.data_dir)

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

def _token(request: Request) -> str:
    tok = request.query_params.get("token") or ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        tok = auth[7:].strip()
    return tok


def whoami(request: Request) -> dict | None:
    """La persona a cui appartiene il token (None se il token non è valido)."""
    tok = _token(request)
    if not tok:
        return None
    if hmac.compare_digest(tok, TOKEN):
        return people.get(OWNER)
    return people.by_token(tok)


def authorized(request: Request) -> bool:
    return whoami(request) is not None


def deny():
    return JSONResponse({"error": "token mancante o errato"}, status_code=401)


def forbidden(msg="Non puoi modificare i dati di un'altra persona"):
    return JSONResponse({"error": msg}, status_code=403)


def bad(msg):
    return JSONResponse({"error": msg}, status_code=400)


def target(request: Request, me: dict) -> dict | None:
    """Persona da mostrare: ?persona=ID, altrimenti chi fa la richiesta."""
    pid = request.query_params.get("persona") or me["id"]
    return people.get(pid)


def portal_absences():
    return [x for day in store.data["days"].values() for x in day if x["kind"] == "absence"]


def shifts_of(person: dict) -> list[dict]:
    return store.shifts() if person["source"] == "portale" else people.shifts(person["id"])


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
    me = whoami(request)
    if not me:
        return deny()
    person = target(request, me)
    if not person:
        return JSONResponse({"error": "persona non trovata"}, status_code=404)
    try:
        base = {
            "today": date.today().isoformat(),
            "now": datetime.now().isoformat(timespec="minutes"),
            "me": me["id"],
            "person": {"id": person["id"], "name": person["name"], "source": person["source"]},
            "people": people.public(),
            "editable": person["source"] == "manuale" and person["id"] == me["id"],
            "is_owner": me["id"] == OWNER,
        }
        if person["source"] == "portale":
            days = store.data["days"]
            base.update({
                "shifts": store.shifts(),
                "known_dates": store.known_dates(),
                "offers": [s for day in days.values() for s in day if s["kind"] == "offer"],
                "notes": [s for day in days.values() for s in day if s["kind"] in ("note", "absence")],
                "changes": store.data.get("changes", [])[:10],
                "status": status_payload(),
            })
        else:
            base.update({
                "shifts": people.shifts(person["id"]),
                "known_dates": [],
                "offers": [], "notes": [], "changes": [],
                "status": {"manual": True, "configured": True, "running": False},
                "templates": people.templates(person["id"]) if base["editable"] else [],
            })
        return JSONResponse(base)
    except Exception as e:
        log(f"Errore in /api/data: {e}\n{traceback.format_exc()}")
        return JSONResponse({"error": f"{type(e).__name__}: {e}"}, status_code=500,
                            headers={"X-Errore": type(e).__name__})


async def api_next(request: Request):
    """Prossimo turno, comodo anche per un sensore REST di Home Assistant."""
    me = whoami(request)
    if not me:
        return deny()
    person = target(request, me) or me
    now = datetime.now().isoformat(timespec="minutes")
    nxt = next((s for s in shifts_of(person) if s["end"] > now and not s.get("absence")), None)
    if not nxt:
        return JSONResponse({"state": None, "persona": person["name"]})
    return JSONResponse({
        "state": nxt["start"], "start": nxt["start"], "end": nxt["end"], "persona": person["name"],
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
    me = whoami(request)
    if not me:
        return Response("token mancante o errato", status_code=401)
    person = target(request, me) or me
    q = request.query_params
    try:
        alarm = int(q.get("avviso", "0"))
    except ValueError:
        alarm = 0
    portal = person["source"] == "portale"
    body = build_ics(shifts_of(person), Config.tz, with_breaks=q.get("pause") == "1", alarm_min=alarm,
                     absences=portal_absences() if portal else [],
                     cal_name=f"Turni di {person['name']}" if person["id"] != OWNER else "Turni di lavoro")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="turni.ics"', "Cache-Control": "no-cache"})


# ---------------------------------------------------------------- persone

def _person_out(p: dict, with_token: bool) -> dict:
    out = {"id": p["id"], "name": p["name"], "source": p["source"]}
    if with_token:
        out["token"] = TOKEN if p["id"] == OWNER else p.get("token")
    return out


async def api_people(request: Request):
    me = whoami(request)
    if not me:
        return deny()
    owner = me["id"] == OWNER
    if request.method == "GET":
        return JSONResponse({"people": [_person_out(p, owner) for p in people.data["people"]]})
    if not owner:
        return forbidden("Solo il titolare può aggiungere persone")
    try:
        body = await request.json()
        p = people.add(body.get("name"))
    except InvalidInput as e:
        return bad(str(e))
    except ValueError:
        return bad("Richiesta non valida")
    log(f"Aggiunta persona: {p['name']}")
    return JSONResponse(_person_out(p, True))


async def api_person(request: Request):
    me = whoami(request)
    if not me:
        return deny()
    pid = request.path_params["pid"]
    owner = me["id"] == OWNER
    if not people.get(pid):
        return JSONResponse({"error": "persona non trovata"}, status_code=404)
    if request.method == "DELETE":
        if not owner or pid == OWNER:
            return forbidden("Solo il titolare può rimuovere le altre persone")
        people.remove(pid)
        log(f"Rimossa persona {pid}")
        return JSONResponse({"ok": True})
    # PUT: cambia nome (il titolare per tutti, gli altri solo per sé)
    if not owner and pid != me["id"]:
        return forbidden()
    try:
        body = await request.json()
        p = people.rename(pid, body.get("name"))
    except InvalidInput as e:
        return bad(str(e))
    except ValueError:
        return bad("Richiesta non valida")
    return JSONResponse(_person_out(p, owner))


async def api_person_token(request: Request):
    me = whoami(request)
    if not me:
        return deny()
    if me["id"] != OWNER:
        return forbidden("Solo il titolare può creare un nuovo link")
    pid = request.path_params["pid"]
    if pid == OWNER:
        return bad("Il tuo token si cambia con ACCESS_TOKEN nelle impostazioni del container")
    try:
        p = people.new_token(pid)
    except KeyError:
        return JSONResponse({"error": "persona non trovata"}, status_code=404)
    return JSONResponse(_person_out(p, True))


# ---------------------------------------------------------------- turni a mano

def _manual_me(request: Request):
    me = whoami(request)
    if not me:
        return None, deny()
    if me["source"] != "manuale":
        return None, forbidden("I tuoi turni arrivano dal portale e non si inseriscono a mano")
    return me, None


async def api_manual_day(request: Request):
    me, err = _manual_me(request)
    if err:
        return err
    day = request.path_params["day"]
    try:
        if request.method == "DELETE":
            people.delete_shift(me["id"], day)
            return JSONResponse({"ok": True})
        body = await request.json()
        return JSONResponse(people.save_shift(me["id"], day, body))
    except InvalidInput as e:
        return bad(str(e))
    except ValueError:
        return bad("Richiesta non valida")


async def api_manual_bulk(request: Request):
    me, err = _manual_me(request)
    if err:
        return err
    try:
        body = await request.json()
        days = body.get("days")
        if not isinstance(days, list) or not days:
            return bad("Scegli almeno un giorno")
        n = people.save_shifts_bulk(me["id"], days, body)
    except InvalidInput as e:
        return bad(str(e))
    except ValueError:
        return bad("Richiesta non valida")
    return JSONResponse({"ok": True, "saved": n})


async def api_templates(request: Request):
    me, err = _manual_me(request)
    if err:
        return err
    if request.method == "GET":
        return JSONResponse({"templates": people.templates(me["id"])})
    try:
        body = await request.json()
        return JSONResponse(people.save_template(me["id"], body))
    except InvalidInput as e:
        return bad(str(e))
    except ValueError:
        return bad("Richiesta non valida")


async def api_template(request: Request):
    me, err = _manual_me(request)
    if err:
        return err
    people.delete_template(me["id"], request.path_params["tid"])
    return JSONResponse({"ok": True})


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
        Route("/api/people", api_people, methods=["GET", "POST"]),
        Route("/api/people/{pid}", api_person, methods=["PUT", "DELETE"]),
        Route("/api/people/{pid}/token", api_person_token, methods=["POST"]),
        Route("/api/manual/shifts", api_manual_bulk, methods=["POST"]),
        Route("/api/manual/shifts/{day}", api_manual_day, methods=["PUT", "DELETE"]),
        Route("/api/manual/templates", api_templates, methods=["GET", "POST"]),
        Route("/api/manual/templates/{tid}", api_template, methods=["DELETE"]),
        Route("/calendar.ics", calendar_ics),
        Route("/turni.ics", calendar_ics),
        Mount("/static", StaticFiles(directory=STATIC), name="static"),
    ],
    lifespan=lifespan,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=Config.port, log_level="warning")
