#!/usr/bin/env python3
"""Směny SPORT – čte rozpis směn z Google tabulky (veřejný CSV export).

Příkazy (viz SKILL.md):
  next  [--person JMÉNO] [-n 5]        nejbližší směny
  day   DATUM                           kdo dělá v daný den (dnes, zítra, pátek, 23.10., 2026-10-23)
  month [MĚSÍC] [--person JMÉNO]        měsíc (2026-11, listopad, 11); s osobou jen její směny
  json  [--out SOUBOR]                  všechna data jako JSON
  ics   [--person JMÉNO] [--since DATUM] --out SOUBOR   export do kalendáře
  build --out SOUBOR [--person JMÉNO] [--standalone|--public]   HTML přehled (šablona assets/wrapper.html)
  check                                 kontrola tabulky (součty, neexistující dny, neznámé kódy)
  set-artifact URL                      uloží URL publikovaného přehledu do config.json
"""
from __future__ import annotations

import argparse
import calendar
import csv
import datetime as dt
import io
import json
import os
import pathlib
import re
import sys
import unicodedata
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
TEMPLATE_PATH = ROOT / "assets" / "wrapper.html"

MONTHS = {
    "leden": 1, "únor": 2, "březen": 3, "duben": 4, "květen": 5, "červen": 6,
    "červenec": 7, "srpen": 8, "září": 9, "říjen": 10, "listopad": 11, "prosinec": 12,
}
MONTH_LABEL = {v: k for k, v in MONTHS.items()}
WEEKDAYS = ["pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle"]
WD_SHORT = ["po", "út", "st", "čt", "pá", "so", "ne"]
TIME_RE = re.compile(r"^(\d{1,2})[:.](\d{2})$")

# Artifact si stránku obalí kostrou sám; samostatný soubor potřebuje vlastní (kódování, viewport a stejný reset jako Artifact).
STANDALONE_HEAD = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<style>:root{color-scheme:light;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px)}body{margin:0;font:14px system-ui,sans-serif}img{max-width:100%}[hidden]{display:none!important}</style>
</head>
<body>
"""


# ---------- drobnosti ----------

def norm(s: str | None) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def fold(s: str | None) -> str:
    """Malá písmena bez diakritiky – pro porovnávání jmen a slov."""
    s = unicodedata.normalize("NFKD", norm(s).lower())
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", fold(s)).strip("-")


def to_num(s: str | None):
    s = norm(s).replace(",", ".")
    if not re.fullmatch(r"-?\d+(\.\d+)?", s):
        return None
    f = float(s)
    return int(f) if f.is_integer() else f


def to_time(s: str | None) -> str | None:
    m = TIME_RE.match(norm(s))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 24 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"


def plural(n, one, few, many):
    n = abs(n)
    return one if n == 1 else few if 2 <= n <= 4 else many


def fmt_num(n) -> str:
    return str(n).replace(".", ",")


def fmt_time(t: str) -> str:
    h, m = t.split(":")
    return f"{int(h)}:{m}"


def fmt_shift(s: dict) -> str:
    if s.get("start"):
        return f"{fmt_time(s['start'])}–{fmt_time(s['end'])}"
    if s.get("hours") is not None:
        return f"{fmt_num(s['hours'])} h, čas neuveden"
    return s["raw"]


def fmt_date(d: dt.date) -> str:
    return f"{WD_SHORT[d.weekday()]} {d.day}. {d.month}."


def rel_day(d: dt.date, today: dt.date) -> str:
    n = (d - today).days
    named = {0: "dnes", 1: "zítra", 2: "pozítří", -1: "včera"}
    if n in named:
        return named[n]
    return f"za {n} {plural(n, 'den', 'dny', 'dní')}" if n > 0 else f"před {-n} dny"


# ---------- konfigurace a stahování ----------

def load_config() -> dict:
    """config.json (lokálně), jinak config.example.json (CI); proměnné prostředí mají přednost."""
    path = CONFIG_PATH if CONFIG_PATH.exists() else ROOT / "config.example.json"
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for key, env in (("sheet_id", "SMENY_SHEET_ID"), ("team", "SMENY_TEAM"), ("default_person", "SMENY_PERSON")):
        if os.environ.get(env):
            cfg[key] = os.environ[env]
    if not cfg.get("sheet_id") or cfg["sheet_id"] == "ID_TABULKY":
        raise SystemExit("Chybí ID tabulky: vyplň sheet_id v config.json nebo nastav proměnnou SMENY_SHEET_ID.")
    return cfg


def save_config(cfg: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (smeny skill)"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")
    except urllib.error.URLError as e:
        raise SystemExit(f"Nepodařilo se stáhnout tabulku ({e}). Je online a pořád veřejně čitelná?")


def list_tabs(sheet_id: str) -> list[dict]:
    page = fetch(f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview")
    tabs: dict[str, str] = {}
    pattern = r'name: "((?:[^"\\]|\\.)*)", pageUrl: "(?:[^"\\]|\\.)*", gid: "(\d+)"'
    for m in re.finditer(pattern, page):
        raw = re.sub(r"\\x([0-9a-fA-F]{2})", r"\\u00\1", m.group(1))
        tabs.setdefault(m.group(2), json.loads(f'"{raw}"'))
    if not tabs:
        raise SystemExit("Nepodařilo se načíst seznam listů tabulky (je pořád veřejně čitelná?).")
    return [{"gid": gid, "name": name} for gid, name in tabs.items()]


def fetch_rows(sheet_id: str, gid: str) -> list[list[str]]:
    text = fetch(f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}")
    if text.lstrip().lower().startswith("<!doctype html"):
        raise SystemExit("Google místo dat vrátil přihlašovací stránku – tabulka asi přestala být veřejná.")
    rows = list(csv.reader(io.StringIO(text)))
    width = max((len(r) for r in rows), default=0)
    return [r + [""] * (width - len(r)) for r in rows]


def month_of_tab(name: str, team: str):
    m = re.fullmatch(rf"{re.escape(team)}\s+(\S+)\s+(\d{{4}})", norm(name), re.I)
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    return None


# ---------- parsování ----------

def parse_month(rows: list[list[str]], year: int, month: int) -> dict:
    """List „SPORT <měsíc> <rok>“: 2 řádky jmen, řádky dnů, pod nimi poznámky, součty a legenda kódů."""
    warnings: list[str] = []
    head0, head1 = rows[0], rows[1]
    people: list[tuple[int, str]] = []
    for c in range(2, len(head0)):
        name = norm(f"{head0[c]} {head1[c]}")
        if name:
            people.append((c, name))
        elif people:
            break
    if not people:
        raise SystemExit(f"List {MONTH_LABEL[month]} {year}: v hlavičce nejsou jména.")
    last_col = people[-1][0]
    ndays = calendar.monthrange(year, month)[1]

    days, last_day_row = [], 1
    for i, row in enumerate(rows[2:], start=2):
        d, wd = to_num(row[0]), norm(row[1]).lower()
        if not (isinstance(d, int) and wd in WEEKDAYS):
            continue
        last_day_row = i
        if not 1 <= d <= ndays:
            filled = [f"{name} {norm(row[c])}" for c, name in people if norm(row[c])]
            if filled:
                warnings.append(f"Řádek „{d}. {wd}“ neexistuje ({MONTH_LABEL[month]} má {ndays} dní), "
                                f"a přesto obsahuje: {', '.join(filled)}. Přeskočeno.")
            continue
        date = dt.date(year, month, d)
        if WEEKDAYS[date.weekday()] != wd:
            warnings.append(f"{d}. {month}. je v tabulce „{wd}“, ve skutečnosti {WEEKDAYS[date.weekday()]}.")
        total, notes = None, []
        for x in (norm(v) for v in row[last_col + 1:]):
            if not x:
                continue
            n = to_num(x)
            if n is not None and total is None and not notes:
                total = n
            else:
                notes.append(x)
        days.append({
            "day": d,
            "date": date.isoformat(),
            "weekday": WEEKDAYS[date.weekday()],
            "weekend": date.weekday() >= 5,
            "total": total,
            "notes": notes,
            "shifts": [{"person": name, "raw": norm(row[c])} for c, name in people if norm(row[c])],
        })

    # Pod dny: poznámky k lidem, řádek součtů, legenda (kód, od, do), sekce VÍKEND, DENNÍ VYBLOKOVÁNO.
    legend: dict[str, dict[str, list[str]]] = {"weekday": {}, "weekend": {}}
    totals: dict[str, float] = {}
    footnotes: dict[str, list[str]] = {name: [] for _, name in people}
    section: str | None = "weekday"
    for row in rows[last_day_row + 1:]:
        cells = [norm(v) for v in row]
        upper = [c.upper() for c in cells]
        if "VÍKEND" in upper:
            section = "weekend"
            continue
        if "VYBLOKOVÁNO" in upper:
            section = None
            continue
        for k in range(len(cells) - 2):
            code, start, end = to_num(cells[k]), to_time(cells[k + 1]), to_time(cells[k + 2])
            if code is not None and start and end:
                if section:
                    legend[section].setdefault(str(code), [start, end])
                break
        else:
            if cells[0]:
                continue  # např. řádek s hodinami 6–20
            vals = {name: cells[c] for c, name in people if cells[c]}
            if not vals:
                continue
            if not totals and all(to_num(cells[c]) is not None for c, _ in people):
                totals = {name: to_num(cells[c]) for c, name in people}
            else:
                for name, v in vals.items():
                    footnotes[name].append(v)

    hours = {name: 0 for _, name in people}
    counts = {name: 0 for _, name in people}
    unknown: dict[str, list[str]] = {}
    for day in days:
        own, other = ("weekend", "weekday") if day["weekend"] else ("weekday", "weekend")
        for s in day["shifts"]:
            n = to_num(s["raw"])
            s["hours"] = n
            s["start"] = s["end"] = None
            s["timeSource"] = None
            if n is not None:
                hours[s["person"]] += n
                counts[s["person"]] += 1
                key = str(n)
                if key in legend[own]:
                    s["start"], s["end"] = legend[own][key]
                    s["timeSource"] = "legend"
                elif key in legend[other]:
                    s["start"], s["end"] = legend[other][key]
                    s["timeSource"] = "legend-" + other
            if not s["start"]:
                unknown.setdefault(s["raw"], []).append(f"{s['person']} {day['day']}. {month}.")
    for code, where in unknown.items():
        what = "Kód" if to_num(code) is not None else "Hodnota"
        more = f" a další ({len(where) - 4})" if len(where) > 4 else ""
        warnings.append(f"{what} „{code}“ není v legendě, čas začátku neznám ({'; '.join(where[:4])}{more})")
    for name, t in totals.items():
        if t is not None and abs(hours[name] - t) > 0.01:
            warnings.append(f"{name}: směny dávají {fmt_num(hours[name])} h, v řádku součtů je {fmt_num(t)} h.")

    return {
        "key": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "label": f"{MONTH_LABEL[month]} {year}",
        "people": [name for _, name in people],
        "legend": legend,
        "days": days,
        "hours": hours,
        "shiftCount": counts,
        "sheetTotals": totals,
        "footnotes": {n: v for n, v in footnotes.items() if v},
        "warnings": warnings,
    }


def parse_vacations(rows: list[list[str]]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    if len(rows) < 2:
        return out
    for c in range(len(rows[0])):
        name = norm(f"{rows[0][c]} {rows[1][c]}")
        vals = [norm(r[c]) for r in rows[2:] if norm(r[c])]
        if name and vals:
            out[name] = vals
    return out


def load_all(cfg: dict) -> dict:
    sid, team = cfg["sheet_id"], cfg["team"]
    tabs = list_tabs(sid)
    months = []
    for t in tabs:
        ym = month_of_tab(t["name"], team)
        if ym:
            m = parse_month(fetch_rows(sid, t["gid"]), *ym)
            m.update(tab=t["name"], gid=t["gid"])
            months.append(m)
    months.sort(key=lambda m: m["key"])
    people: list[str] = []
    for m in months:
        people += [p for p in m["people"] if p not in people]

    vacations: dict[str, list[str]] = {}
    vac_tab = next((t for t in tabs if fold(t["name"]) == fold(cfg.get("vacations_tab"))), None)
    if vac_tab:
        by_fold = {fold(p): p for p in people}
        for name, vals in parse_vacations(fetch_rows(sid, vac_tab["gid"])).items():
            if fold(name) in by_fold:
                vacations[by_fold[fold(name)]] = vals

    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
        "team": team,
        "sheetUrl": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
        "defaultPerson": cfg.get("default_person"),
        "people": people,
        "months": months,
        "vacations": vacations,
    }


# ---------- dotazy ----------

def all_shifts(data: dict):
    for m in data["months"]:
        for day in m["days"]:
            for s in day["shifts"]:
                yield day, s


def shift_bounds(day: dict, s: dict):
    base = dt.datetime.combine(dt.date.fromisoformat(day["date"]), dt.time())
    if not s["start"]:
        return base, base + dt.timedelta(days=1), False

    def at(t):
        h, m = map(int, t.split(":"))
        return base + dt.timedelta(hours=h, minutes=m)

    st, en = at(s["start"]), at(s["end"])
    if en <= st:
        en += dt.timedelta(days=1)
    return st, en, True


def find_person(data: dict, query: str | None) -> str:
    q = fold(query or data.get("defaultPerson") or "")
    people = data["people"]
    exact = [p for p in people if fold(p) == q]
    if exact:
        return exact[0]
    hits = [p for p in people if q and all(any(w.startswith(tok) for w in fold(p).split()) for tok in q.split())]
    if len(hits) == 1:
        return hits[0]
    raise SystemExit(f"Osobu „{query}“ jsem {'nenašel' if not hits else 'nenašel jednoznačně'}. "
                     f"V týmu {data['team']}: {', '.join(hits or people)}.")


def parse_date_arg(s: str | None, today: dt.date) -> dt.date:
    f = fold(s or "dnes")
    rel = {"dnes": 0, "today": 0, "zitra": 1, "tomorrow": 1, "pozitri": 2}
    if f in rel:
        return today + dt.timedelta(days=rel[f])
    wd = [fold(w) for w in WEEKDAYS]
    for i, name in enumerate(wd):
        if f in (name, WD_SHORT[i], fold(WD_SHORT[i])) or (len(f) >= 3 and name.startswith(f)):
            return today + dt.timedelta(days=(i - today.weekday()) % 7)
    try:
        return dt.date.fromisoformat(f)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})\.\s*(\d{1,2})\.?\s*(\d{4})?", f)
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        if m.group(3):
            return dt.date(int(m.group(3)), mo, d)
        cand = dt.date(today.year, mo, d)
        return cand if (today - cand).days < 183 else dt.date(today.year + 1, mo, d)
    raise SystemExit(f"Datum „{s}“ neznám. Zkus: dnes, zítra, pátek, 23.10., 2026-10-23.")


def pick_month(data: dict, arg: str | None, today: dt.date) -> dict:
    months = data["months"]
    if not months:
        raise SystemExit("V tabulce není žádný list s měsícem.")
    if arg:
        f = fold(arg)
        for m in months:
            if f in (m["key"], fold(m["label"]), fold(MONTH_LABEL[m["month"]]), str(m["month"]), f"{m['month']:02d}"):
                return m
        raise SystemExit(f"Měsíc „{arg}“ v tabulce není. K dispozici: {', '.join(m['label'] for m in months)}.")
    key = f"{today.year}-{today.month:02d}"
    return next((m for m in months if m["key"] == key), None) or \
        next((m for m in months if m["key"] > key), months[-1])


def cmd_next(data, args):
    person = find_person(data, args.person)
    now = dt.datetime.now()
    items = []
    for day, s in all_shifts(data):
        if s["person"] == person:
            st, en, timed = shift_bounds(day, s)
            if en > now:
                items.append((st, en, timed, day, s))
    items.sort(key=lambda x: x[0])
    print(f"{person} – nejbližší směny (tým {data['team']}, teď je {fmt_date(now.date())} {now:%H:%M})")
    if not items:
        print("  V tabulce už žádná další směna není.")
    for st, en, timed, day, s in items[: args.n]:
        status = "PROBÍHÁ" if timed and st <= now < en else rel_day(st.date(), now.date())
        line = f"  {status:<10} {fmt_date(st.date()):<11} {fmt_shift(s)}"
        if s["start"]:
            line += f" ({fmt_num(s['hours'])} h)"
        others = [f"{o['person']} {fmt_shift(o)}" for o in day["shifts"] if o["person"] != person]
        line += "  · ve stejný den: " + (", ".join(others) if others else "nikdo další")
        if day["notes"]:
            line += "  · pozn.: " + "; ".join(day["notes"])
        print(line)
    print(f"\nRozpis v tabulce sahá do: {data['months'][-1]['label'] if data['months'] else '–'}.")


def cmd_day(data, args):
    today = dt.date.today()
    d = parse_date_arg(args.date, today)
    day = next((x for m in data["months"] for x in m["days"] if x["date"] == d.isoformat()), None)
    print(f"{WEEKDAYS[d.weekday()]} {d.day}. {d.month}. {d.year} ({rel_day(d, today)}) – tým {data['team']}")
    if day is None:
        print("  Tenhle den v tabulce není (list pro ten měsíc zatím neexistuje).")
        return
    if not day["shifts"]:
        print("  Nikdo nemá směnu.")
    for s in sorted(day["shifts"], key=lambda s: s["start"] or "99"):
        print(f"  {s['person']:<16} {fmt_shift(s)}  (kód {s['raw']})")
    if day["notes"]:
        print("  pozn.: " + "; ".join(day["notes"]))


def cmd_month(data, args):
    today = dt.date.today()
    m = pick_month(data, args.month, today)
    if args.person:
        person = find_person(data, args.person)
        print(f"{person} – {m['label']} (tým {data['team']})")
        for day in m["days"]:
            for s in day["shifts"]:
                if s["person"] == person:
                    d = dt.date.fromisoformat(day["date"])
                    flag = "  ← dnes" if d == today else ""
                    print(f"  {fmt_date(d):<11} {fmt_shift(s)}{flag}")
        total = m["sheetTotals"].get(person)
        print(f"  Celkem: {m['shiftCount'][person]} {plural(m['shiftCount'][person], 'směna', 'směny', 'směn')}, "
              f"{fmt_num(m['hours'][person])} h" + (f" (v tabulce {fmt_num(total)} h)" if total is not None
                                                      and total != m["hours"][person] else ""))
        for note in m["footnotes"].get(person, []):
            print(f"  Poznámka v tabulce: {note}")
        return
    print(f"{m['label']} – tým {data['team']}")
    for day in m["days"]:
        d = dt.date.fromisoformat(day["date"])
        parts = [f"{s['person']} {fmt_shift(s)}" for s in sorted(day["shifts"], key=lambda s: s["start"] or "99")]
        note = f"  [{'; '.join(day['notes'])}]" if day["notes"] else ""
        print(f"  {fmt_date(d):<11} {' | '.join(parts) or '—'}{note}")
    print("  Hodiny: " + ", ".join(f"{p} {fmt_num(h)} h" for p, h in m["hours"].items()))


def cmd_json(data, args):
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if args.out:
        pathlib.Path(args.out).write_text(text, encoding="utf-8")
        print(args.out)
    else:
        print(text)


def ics_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def ics_fold(line: str) -> list[str]:
    out, cur = [], ""
    for ch in line:
        if len((cur + ch).encode("utf-8")) > (75 if not out else 74):
            out.append(cur)
            cur = ch
        else:
            cur += ch
    out.append(cur)
    return [out[0]] + [" " + x for x in out[1:]]


VTIMEZONE = """BEGIN:VTIMEZONE
TZID:Europe/Prague
BEGIN:DAYLIGHT
TZOFFSETFROM:+0100
TZOFFSETTO:+0200
TZNAME:CEST
DTSTART:19700329T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:+0200
TZOFFSETTO:+0100
TZNAME:CET
DTSTART:19701025T030000
RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU
END:STANDARD
END:VTIMEZONE""".splitlines()


def cmd_ics(data, args):
    person = find_person(data, args.person)
    today = dt.date.today()
    since = parse_date_arg(args.since, today) if args.since else today
    team = data["team"]
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//smeny-skill//CS", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:Směny {team}", "X-WR-TIMEZONE:Europe/Prague", *VTIMEZONE]
    count = 0
    for day, s in all_shifts(data):
        if s["person"] != person or dt.date.fromisoformat(day["date"]) < since:
            continue
        st, en, timed = shift_bounds(day, s)
        others = [f"{o['person']} {fmt_shift(o)}" for o in day["shifts"] if o["person"] != person]
        desc = [f"Kód v tabulce: {s['raw']}"]
        desc.append("Ve stejný den: " + (", ".join(others) if others else "nikdo další"))
        if day["notes"]:
            desc.append("Poznámka: " + "; ".join(day["notes"]))
        desc.append(f"Zdroj: {data['sheetUrl']}")
        summary = f"Směna {team} {fmt_shift(s)}" if timed else f"Směna {team} ({fmt_shift(s)})"
        ev = ["BEGIN:VEVENT", f"UID:{day['date']}-{slug(person)}-{slug(team)}@smeny", f"DTSTAMP:{stamp}"]
        if timed:
            ev += [f"DTSTART;TZID=Europe/Prague:{st:%Y%m%dT%H%M%S}", f"DTEND;TZID=Europe/Prague:{en:%Y%m%dT%H%M%S}"]
        else:
            ev += [f"DTSTART;VALUE=DATE:{st:%Y%m%d}", f"DTEND;VALUE=DATE:{en:%Y%m%d}"]
        ev += [f"SUMMARY:{ics_escape(summary)}", f"DESCRIPTION:{ics_escape(chr(10).join(desc))}",
               "TRANSP:OPAQUE", "END:VEVENT"]
        lines += ev
        count += 1
    lines.append("END:VCALENDAR")
    out = pathlib.Path(args.out)
    out.write_bytes("\r\n".join(x for line in lines for x in ics_fold(line)).encode("utf-8") + b"\r\n")
    print(f"{out} – {count} {plural(count, 'směna', 'směny', 'směn')} pro {person} od {fmt_date(since)}")


def cmd_build(data, args):
    if args.person:
        data["defaultPerson"] = find_person(data, args.person)
    if args.public:
        # Veřejná stránka: bez odkazu na zdrojovou tabulku (obsahuje i další listy) a bez plánovaných dovolených.
        data["public"] = True
        data["sheetUrl"] = ""
        data["vacations"] = {}
        args.standalone = True
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = TEMPLATE_PATH.read_text(encoding="utf-8").replace("__SMENY_DATA__", payload, 1)
    if args.standalone:
        head = STANDALONE_HEAD
        if args.public:
            head = head.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n<meta name="robots" content="noindex,nofollow">')
        html = head + html + "\n</body>\n</html>\n"
    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"{out} – {len(data['months'])} měs., data z {data['generated']}")


def cmd_check(data, args):
    print(f"Tým {data['team']}, lidé: {', '.join(data['people'])}")
    for m in data["months"]:
        print(f"\n{m['label']} (list „{m['tab']}“): {len(m['days'])} dnů, "
              + ", ".join(f"{p} {fmt_num(h)} h" for p, h in m["hours"].items()))
        print("  legenda: " + "; ".join(f"{sec} {code} = {fmt_time(a)}–{fmt_time(b)}"
                                       for sec, codes in m["legend"].items() for code, (a, b) in codes.items()))
        for w in m["warnings"] or ["bez problémů"]:
            print(f"  ! {w}" if m["warnings"] else f"  {w}")
    if data["vacations"]:
        print("\nPlánované dovolené: " + "; ".join(f"{p}: {', '.join(v)}" for p, v in data["vacations"].items()))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Směny z Google tabulky")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("next"); p.add_argument("--person"); p.add_argument("-n", type=int, default=5)
    p = sub.add_parser("day"); p.add_argument("date", nargs="?", default="dnes")
    p = sub.add_parser("month"); p.add_argument("month", nargs="?"); p.add_argument("--person")
    p = sub.add_parser("json"); p.add_argument("--out")
    p = sub.add_parser("ics"); p.add_argument("--person"); p.add_argument("--since"); p.add_argument("--out", required=True)
    p = sub.add_parser("build"); p.add_argument("--out", required=True); p.add_argument("--person")
    p.add_argument("--standalone", action="store_true", help="celý HTML dokument pro otevření mimo Artifact")
    p.add_argument("--public", action="store_true", help="verze pro veřejný web: bez odkazu na tabulku a bez dovolených, noindex")
    sub.add_parser("check")
    p = sub.add_parser("set-artifact"); p.add_argument("url")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "set-artifact":
        cfg["artifact_url"] = args.url
        save_config(cfg)
        print(f"Uloženo: {args.url}")
        return
    data = load_all(cfg)
    {"next": cmd_next, "day": cmd_day, "month": cmd_month, "json": cmd_json, "ics": cmd_ics,
     "build": cmd_build, "check": cmd_check}[args.cmd](data, args)


if __name__ == "__main__":
    main()
