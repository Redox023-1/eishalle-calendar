import json
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

URL = "https://www.augsburg.de/freizeit/sport/wintersport/eishalle-haunstetten"
ROOT = Path(__file__).parent
OUT = ROOT / "docs" / "eishalle.ics"
OVERRIDES = ROOT / "data" / "overrides.json"
HEARTBEAT = ROOT / "data" / "heartbeat.txt"
LOCATION = "Eishalle Haunstetten, Sportplatzstraße 2, 86179 Augsburg"
DAYS = {"Mo": 0, "Di": 1, "Mi": 2, "Do": 3, "Fr": 4, "Sa": 5, "So": 6}
T = r"\d{1,2}[:.]\d{2}\s*[–—-]\s*\d{1,2}[:.]\d{2}"
TG = r"(\d{1,2})[:.](\d{2})\s*[–—-]\s*(\d{1,2})[:.](\d{2})"
SLOTS = rf"(geschlossen|{T}(?:\s*Uhr)?(?:\s*(?:\||,|und|/)\s*{T}(?:\s*Uhr)?)*)"


def fetch_text(source=None):
    if source:
        html = Path(source).read_text(encoding="utf-8")
    else:
        r = requests.get(URL, headers={"User-Agent": "Mozilla/5.0 (eishalle-ics)"}, timeout=30)
        r.raise_for_status()
        html = r.text
    text = BeautifulSoup(html, "html.parser").get_text(" ")
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ").replace("\u202f", " "))


def parse_slots(s):
    if "geschlossen" in s.lower():
        return []
    return [[f"{int(a):02d}:{b}", f"{int(c):02d}:{d}"] for a, b, c, d in re.findall(TG, s)]


def parse_weekly(text):
    week = {}
    for m in re.finditer(rf"\b(Mo|Di|Mi|Do|Fr|Sa|So)\b\.?\s*:?\s*{SLOTS}", text, re.I):
        day = DAYS[m.group(1).capitalize()]
        week.setdefault(day, parse_slots(m.group(2)))
    if len(week) < 7:
        sys.exit(f"Wochenplan nicht gefunden: {len(week)}/7 Tage erkannt")
    if not any(week.values()):
        sys.exit("Wochenplan enthält keine Zeiten")
    return week


def parse_today_tomorrow(text):
    found = {}
    for m in re.finditer(rf"\b(?:Heute|Morgen)\b,?\s*(\d{{1,2}})\.(\d{{1,2}})\.(\d{{4}})\s*:?\s*{SLOTS}", text, re.I):
        d = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        found[d.isoformat()] = parse_slots(m.group(4))
    return found


def season_bounds(today):
    start_year = today.year if today.month >= 9 else today.year - 1
    start, end = date(start_year, 9, 15), date(start_year + 1, 3, 31)
    if today > end:
        start, end = date(today.year, 9, 15), date(today.year + 1, 3, 31)
    return start, end


def fold(line):
    raw, parts = line.encode("utf-8"), []
    limit = 75
    while len(raw) > limit:
        i = limit
        while (raw[i] & 0xC0) == 0x80:
            i -= 1
        parts.append(raw[:i].decode("utf-8"))
        raw = raw[i:]
        limit = 74
    parts.append(raw.decode("utf-8"))
    return "\r\n ".join(parts)


def esc(s):
    return s.replace("\\", "\\\\").replace(";", r"\;").replace(",", "\\,").replace("\n", "\\n")


def build(week, overrides, start, end):
    lines = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//eishalle-calendar//DE", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "X-WR-CALNAME:Eishalle Haunstetten", "X-WR-TIMEZONE:Europe/Berlin",
        "REFRESH-INTERVAL;VALUE=DURATION:PT6H", "X-PUBLISHED-TTL:PT6H",
        "BEGIN:VTIMEZONE", "TZID:Europe/Berlin",
        "BEGIN:DAYLIGHT", "TZOFFSETFROM:+0100", "TZOFFSETTO:+0200", "TZNAME:CEST",
        "DTSTART:19700329T020000", "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU", "END:DAYLIGHT",
        "BEGIN:STANDARD", "TZOFFSETFROM:+0200", "TZOFFSETTO:+0100", "TZNAME:CET",
        "DTSTART:19701025T030000", "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU", "END:STANDARD",
        "END:VTIMEZONE",
    ]
    stamp = "20260101T000000Z"
    desc = esc(f"Publikumslauf. Aktuelle Infos und Preise: {URL}")
    d = start
    while d <= end:
        key = d.isoformat()
        regular = week[d.weekday()]
        changed = key in overrides
        slots = overrides[key] if changed else regular
        ymd = d.strftime("%Y%m%d")
        if changed and not slots and regular:
            nxt = (d + timedelta(days=1)).strftime("%Y%m%d")
            lines += ["BEGIN:VEVENT", f"UID:{ymd}-closed@eishalle-haunstetten", f"DTSTAMP:{stamp}",
                      f"DTSTART;VALUE=DATE:{ymd}", f"DTEND;VALUE=DATE:{nxt}",
                      "SUMMARY:Eishalle Haunstetten geschlossen", f"LOCATION:{esc(LOCATION)}",
                      f"DESCRIPTION:{desc}", "TRANSP:TRANSPARENT", "END:VEVENT"]
        for s, e in slots:
            title = "Publikumslauf Eishalle" + (" (geänderte Zeit)" if changed else "")
            lines += ["BEGIN:VEVENT", f"UID:{ymd}-{s.replace(':', '')}@eishalle-haunstetten",
                      f"DTSTAMP:{stamp}",
                      f"DTSTART;TZID=Europe/Berlin:{ymd}T{s.replace(':', '')}00",
                      f"DTEND;TZID=Europe/Berlin:{ymd}T{e.replace(':', '')}00",
                      f"SUMMARY:{esc(title)}", f"LOCATION:{esc(LOCATION)}",
                      f"DESCRIPTION:{desc}", "TRANSP:TRANSPARENT", "END:VEVENT"]
        d += timedelta(days=1)
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


def main():
    source = sys.argv[1] if len(sys.argv) > 1 else None
    today = date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else date.today()
    text = fetch_text(source)
    week = parse_weekly(text)
    overrides = json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else {}
    for key, slots in parse_today_tomorrow(text).items():
        if slots != week[date.fromisoformat(key).weekday()]:
            overrides[key] = slots
        else:
            overrides.pop(key, None)
    start, end = season_bounds(today)
    overrides = {k: v for k, v in sorted(overrides.items()) if start.isoformat() <= k <= end.isoformat()}
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(json.dumps(overrides, indent=1, ensure_ascii=False) + "\n")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(week, overrides, start, end), encoding="utf-8", newline="")
    last = date.fromisoformat(HEARTBEAT.read_text().strip()) if HEARTBEAT.exists() else date.min
    if (today - last).days >= 30:
        HEARTBEAT.write_text(today.isoformat() + "\n")
    print("Woche:", {k: week[v] for k, v in DAYS.items()})
    print("Abweichungen:", overrides)


if __name__ == "__main__":
    main()
