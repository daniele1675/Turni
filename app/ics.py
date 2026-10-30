"""Feed iCalendar (RFC 5545) dei turni, con orari in UTC per la massima compatibilità."""
import re
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    # Spezza le righe lunghe senza mai separare una sequenza di escape (es. "\n").
    out, cur = [], ""
    for ch in re.findall(r"\\.|.", line, flags=re.S):
        if len((cur + ch).encode("utf-8")) > 73:
            out.append(cur)
            cur = " " + ch
        else:
            cur += ch
    out.append(cur)
    return "\r\n".join(out)


def _utc(local_iso: str, tz: ZoneInfo) -> str:
    dt = datetime.fromisoformat(local_iso).replace(tzinfo=tz)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _hours(minutes: int) -> str:
    h, m = divmod(minutes, 60)
    return f"{h} h" if not m else f"{h} h {m:02d}"


def build_ics(shifts: list[dict], tz_name: str, with_breaks: bool = False, alarm_min: int = 0) -> str:
    tz = ZoneInfo(tz_name)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Turni//Amazon Moment//IT",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Turni di lavoro",
        f"X-WR-TIMEZONE:{tz_name}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
        "X-PUBLISHED-TTL:PT1H",
    ]
    for sh in shifts:
        detail = "\n".join(f'{s["start"][11:16]}–{s["end"][11:16]}  {s["label"]}' for s in sh["segments"])
        title = "Turno " + sh["start"][11:16] + "–" + sh["end"][11:16]
        if sh["highlights"]:
            title += " · " + ", ".join(sh["highlights"])
        desc = f'Ore di lavoro: {_hours(sh["minutes_worked"])} (pranzo escluso)\n\n{detail}'
        lines += [
            "BEGIN:VEVENT",
            f'UID:turno-{sh["start"].replace(":", "").replace("-", "")}@turni.local',
            f"DTSTAMP:{stamp}",
            f'DTSTART:{_utc(sh["start"], tz)}',
            f'DTEND:{_utc(sh["end"], tz)}',
            f"SUMMARY:{_esc(title)}",
            f"DESCRIPTION:{_esc(desc)}",
            "CATEGORIES:Lavoro",
            "TRANSP:OPAQUE",
        ]
        if alarm_min > 0:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Turno di lavoro",
                      f"TRIGGER:-PT{alarm_min}M", "END:VALARM"]
        lines.append("END:VEVENT")
        if with_breaks:
            for s in sh["segments"]:
                if s["kind"] in ("break", "lunch"):
                    lines += [
                        "BEGIN:VEVENT",
                        f'UID:pausa-{s["start"].replace(":", "").replace("-", "")}@turni.local',
                        f"DTSTAMP:{stamp}",
                        f'DTSTART:{_utc(s["start"], tz)}',
                        f'DTEND:{_utc(s["end"], tz)}',
                        f'SUMMARY:{_esc(s["label"])}',
                        "TRANSP:TRANSPARENT",
                        "END:VEVENT",
                    ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"
