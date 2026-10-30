"""Test senza browser: classificazione, turni, modifiche, feed iCal.  python3 -m tests.test_model"""
import copy
import json
import os
import tempfile
from datetime import date, timedelta

from app.ics import build_ics
from app.model import Store, build_shifts, classify, normalize


def seg(d, a, b, title="Work", cls="work", bg=False):
    return {"start": f"{d}T{a}", "end": f"{d}T{b}", "title": title, "classes": cls, "background": bg}


def week(start: date):
    return [(start + timedelta(days=i)).isoformat() for i in range(7)]


def main():
    assert classify("Lunch", "break") == "lunch"
    assert classify("Break", "break") == "break"
    assert classify("Team Meeting", "npt") == "meeting"
    assert classify("Training", "npt") == "training"
    assert classify("Other NPT", "npt") == "npt"
    assert classify("Extra Time", "extratime", True) == "offer"

    base = date.today() + timedelta(days=1)
    d1, d2, d3 = base.isoformat(), (base + timedelta(days=1)).isoformat(), (base + timedelta(days=2)).isoformat()
    w1 = {"week": week(base), "segments": [
        seg(d1, "07:00", "11:00"), seg(d1, "11:00", "11:30", "Lunch", "break"), seg(d1, "11:30", "15:30"),
        seg(d2, "22:00", "23:59"), {"start": f"{d2}T23:59", "end": f"{d3}T06:00", "title": "Work", "classes": "work"},
        seg(d3, "15:00", "18:00", "Extra Time", "extratime", True),
    ]}
    shifts = build_shifts(normalize(w1["segments"]))
    assert [(s["date"], s["start"][11:], s["end"][11:], s["minutes_worked"]) for s in shifts] == [
        (d1, "07:00", "15:30", 480), (d2, "22:00", "06:00", 480)], shifts

    with tempfile.TemporaryDirectory() as tmp:
        st = Store(os.path.join(tmp, "t.json"))
        assert st.apply_weeks([w1]) == []  # prima lettura: nessuna "modifica"
        w2 = copy.deepcopy(w1)
        w2["segments"][0]["start"] = f"{d1}T08:00"                      # turno spostato
        w2["segments"] = [s for s in w2["segments"] if s["start"][:10] != d2 and s["end"][:10] != d3 or s.get("background")]
        w2["segments"].append(seg((base + timedelta(days=4)).isoformat(), "09:00", "17:00"))  # turno nuovo in un giorno di riposo
        ch = st.apply_weeks([w2])
        kinds = sorted((c["date"], c["type"]) for c in ch)
        assert kinds == sorted([(d1, "changed"), (d2, "removed"), ((base + timedelta(days=4)).isoformat(), "added")]), kinds
        st2 = Store(os.path.join(tmp, "t.json"))  # riletto da disco
        assert len(st2.shifts()) == 2

        ics = build_ics(st2.shifts(), "Europe/Rome", with_breaks=True, alarm_min=30)
        assert ics.count("BEGIN:VEVENT") == 3 and "TRIGGER:-PT30M" in ics
        for line in ics.split("\r\n"):
            assert len(line.encode()) <= 75, line
            assert not line.endswith("\\") or line.endswith("\\\\"), "escape spezzato: " + line
    print("ok: modello, modifiche e calendario")


if __name__ == "__main__":
    main()
