"""Test delle API per più persone, contro un server avviato con ACCESS_TOKEN=prova123.

    BASE=http://localhost:8110 python3 -m tests.test_people_api
"""
import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8110")
OWNER = "prova123"


def call(method, path, token, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.startswith(("{", "[")) else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, (json.loads(raw) if raw.startswith("{") else raw)


def main():
    st, d = call("GET", "/api/data", OWNER)
    assert st == 200 and d["me"] == "owner" and d["is_owner"] and not d["editable"], d

    # il titolare aggiunge la seconda persona
    st, p = call("POST", "/api/people", OWNER, {"name": "Giulia"})
    assert st == 200 and p["token"] and p["source"] == "manuale", p
    tok, pid = p["token"], p["id"]

    # la seconda persona non può aggiungere persone né modificare il titolare
    assert call("POST", "/api/people", tok, {"name": "X"})[0] == 403
    assert call("PUT", "/api/people/owner", tok, {"name": "X"})[0] == 403
    assert call("DELETE", f"/api/people/{pid}", tok)[0] == 403
    # il titolare non inserisce turni a mano
    assert call("PUT", "/api/manual/shifts/2026-11-02", OWNER, {"start": "07:00", "end": "15:00"})[0] == 403

    # modelli
    st, t = call("POST", "/api/manual/templates", tok,
                 {"name": "Mattina", "start": "07:00", "end": "15:30", "pause_min": 30, "pause_at": "12:00"})
    assert st == 200 and t["id"], t
    st, t2 = call("POST", "/api/manual/templates", tok, {"name": "Notte", "start": "22:00", "end": "06:00", "pause_min": 0})
    assert st == 200, t2
    assert call("POST", "/api/manual/templates", tok, {"name": "", "start": "07:00", "end": "15:00"})[0] == 400
    assert call("POST", "/api/manual/templates", tok, {"name": "X", "start": "7", "end": "15:00"})[0] == 400
    assert call("POST", "/api/manual/templates", tok, {"name": "X", "start": "07:00", "end": "08:00", "pause_min": 90})[0] == 400

    # turni: uno dal modello, uno notturno, più giorni insieme
    st, sh = call("PUT", "/api/manual/shifts/2026-11-02", tok,
                  {"start": "07:00", "end": "15:30", "pause_min": 30, "pause_at": "12:00", "template": t["id"]})
    assert st == 200 and sh["minutes_worked"] == 480 and sh["segments"][1]["start"].endswith("12:00"), sh
    st, sh = call("PUT", "/api/manual/shifts/2026-11-03", tok, {"start": "22:00", "end": "06:00", "note": "Notte"})
    assert st == 200 and sh["end"] == "2026-11-04T06:00" and sh["minutes_worked"] == 480, sh
    st, r = call("POST", "/api/manual/shifts", tok,
                 {"days": ["2026-11-05", "2026-11-06"], "start": "14:00", "end": "22:00", "pause_min": 30})
    assert st == 200 and r["saved"] == 2, r

    st, d = call("GET", "/api/data", tok)
    assert st == 200 and d["editable"] and len(d["shifts"]) == 4 and len(d["templates"]) == 2, d
    # il titolare vede i turni dell'altra persona, ma non può modificarli
    st, d2 = call("GET", f"/api/data?persona={pid}", OWNER)
    assert st == 200 and len(d2["shifts"]) == 4 and not d2["editable"] and d2["templates"] == [], d2
    # e la seconda persona vede quelli del titolare
    st, d3 = call("GET", "/api/data?persona=owner", tok)
    assert st == 200 and d3["person"]["source"] == "portale", d3

    # calendario personale
    st, ics = call("GET", "/calendar.ics", tok)
    assert st == 200 and "X-WR-CALNAME:Turni di Giulia" in ics and ics.count("BEGIN:VEVENT") == 4, ics[:300]
    st, nxt = call("GET", "/api/next", tok)
    assert st == 200 and nxt["persona"] == "Giulia", nxt

    # cancellazione e nuovo link
    assert call("DELETE", "/api/manual/shifts/2026-11-06", tok)[0] == 200
    assert call("DELETE", f"/api/manual/templates/{t2['id']}", tok)[0] == 200
    st, p2 = call("POST", f"/api/people/{pid}/token", OWNER)
    assert st == 200 and p2["token"] != tok
    assert call("GET", "/api/data", tok)[0] == 401  # il vecchio link non funziona più
    st, d = call("GET", "/api/data", p2["token"])
    assert st == 200 and len(d["shifts"]) == 3 and len(d["templates"]) == 1

    # rinomina e rimozione
    assert call("PUT", f"/api/people/{pid}", p2["token"], {"name": "Giulia R."})[0] == 200
    assert call("PUT", "/api/people/owner", OWNER, {"name": "Daniele"})[0] == 200
    st, lst = call("GET", "/api/people", OWNER)
    assert [x["name"] for x in lst["people"]] == ["Daniele", "Giulia R."], lst
    print("ok: persone, permessi, turni a mano, modelli, calendario")
    return pid


if __name__ == "__main__":
    main()
