#!/usr/bin/env python3
"""Směny SPORT – čte rozpis směn z Google tabulky (veřejný CSV export).

Příkazy (viz SKILL.md), tým volí --team (sport, denni) před příkazem:
  next  [--person JMÉNO] [-n 5]        nejbližší směny
  day   DATUM                           kdo dělá v daný den (dnes, zítra, pátek, 23.10., 2026-10-23)
  month [MĚSÍC] [--person JMÉNO]        měsíc (2026-11, listopad, 11); s osobou jen její směny
  json  [--out SOUBOR]                  všechna data jako JSON
  ics   [--person JMÉNO] [--since DATUM] --out SOUBOR   export do kalendáře
  build --out SOUBOR [--person JMÉNO] [--standalone|--public]   HTML přehled (šablona assets/wrapper.html)
  site  --out SLOŽKA                    veřejný web: rozcestník + složka pro každý tým (GitHub Pages)
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
    if s.get("options"):
        return f"{fmt_num(s['hours'])} h, " + " nebo ".join(f"{fmt_time(a)}–{fmt_time(b)}" for a, b in s["options"])
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


def fetch_colors(sheet_id: str, gid: str) -> dict[tuple[int, int], str]:
    """Barvy pozadí buněk z HTML náhledu listu: {(řádek, sloupec): "#rrggbb"}, bílé vynechané.

    Stejný kód může v legendě znamenat víc časů (DENNÍ: 7 = 12–19 nebo 16–23) a rozliší je jen barva buňky.
    Prázdné barevné buňky značí stav (vyblokováno, dělá v jiném týmu)."""
    try:
        page = fetch(f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview/sheet?headers=false&gid={gid}")
    except SystemExit:
        return {}
    css = {}
    for cls, body in re.findall(r"\.(s\d+)\{([^}]*)\}", page):
        m = re.search(r"background-color:\s*(#[0-9a-fA-F]{6})", body)
        if m:
            css[cls] = m.group(1).lower()
    colors: dict[tuple[int, int], str] = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        rh = re.search(rf'<th[^>]*id="{gid}R(\d+)"', tr)
        if not rh:
            continue
        r, c = int(rh.group(1)), 0
        for attrs in re.findall(r"<td([^>]*)>", tr):
            cls = re.search(r'class="[^"]*?\b(s\d+)\b', attrs)
            color = css.get(cls.group(1)) if cls else None
            if color and color != "#ffffff":
                colors[(r, c)] = color
            span = re.search(r'colspan="(\d+)"', attrs)
            c += int(span.group(1)) if span else 1
    return colors


def month_of_tab(name: str, team: str):
    m = re.fullmatch(rf"{re.escape(team)}\s+(\S+)\s+(\d{{4}})", norm(name), re.I)
    if m and m.group(1).lower() in MONTHS:
        return int(m.group(2)), MONTHS[m.group(1).lower()]
    return None


# ---------- parsování ----------

def parse_month(rows: list[list[str]], year: int, month: int, colors: dict | None = None) -> dict:
    """List „<TÝM> <měsíc> <rok>“: 2 řádky jmen, řádky dnů, pod nimi poznámky, součty a legenda kódů.

    Časy směn se doplní až v resolve_month, protože stavové barvy se sbírají ze všech listů."""
    colors = colors or {}
    warnings: list[str] = []
    status_defs: dict[str, str] = {}
    for r in (0, 1):
        if norm(rows[r][0]) and (r, 0) in colors:
            status_defs[colors[(r, 0)]] = norm(rows[r][0])
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
            "shifts": [{"person": name, "raw": norm(row[c]), "color": colors.get((i, c))}
                       for c, name in people if norm(row[c])],
            "marks": [{"person": name, "color": colors[(i, c)]}
                      for c, name in people if not norm(row[c]) and (i, c) in colors],
        })

    # Pod dny: poznámky k lidem, řádek součtů, legenda (kód, od, do), sekce VÍKEND, DENNÍ VYBLOKOVÁNO.
    legend: dict[str, list[dict]] = {"weekday": [], "weekend": []}
    totals: dict[str, float] = {}
    footnotes: dict[str, list[str]] = {name: [] for _, name in people}
    section: str | None = "weekday"
    for r_i, row in enumerate(rows[last_day_row + 1:], start=last_day_row + 1):
        cells = [norm(v) for v in row]
        upper = [c.upper() for c in cells]
        if "VÍKEND" in upper:
            section = "weekend"
            continue
        if "VYBLOKOVÁNO" in upper:
            # Řádek se stavovými barvami, např. „DENNÍ“ žlutě a „VYBLOKOVÁNO“ červeně.
            for k, text in enumerate(cells):
                if text and (r_i, k) in colors:
                    status_defs[colors[(r_i, k)]] = "vyblokováno" if text.upper() == "VYBLOKOVÁNO" else text
            section = None
            continue
        for k in range(len(cells) - 2):
            code, start, end = to_num(cells[k]), to_time(cells[k + 1]), to_time(cells[k + 2])
            if code is not None and start and end:
                entry = {"code": str(code), "start": start, "end": end, "color": colors.get((r_i, k))}
                if section and entry not in legend[section]:
                    legend[section].append(entry)
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
                # Jen text; další číselné řádky (počty online, částky) se nikam nepropisují.
                for name, v in vals.items():
                    if to_num(v) is None:
                        footnotes[name].append(v)

    return {
        "key": f"{year}-{month:02d}",
        "year": year,
        "month": month,
        "label": f"{MONTH_LABEL[month]} {year}",
        "people": [name for _, name in people],
        "legend": legend,
        "days": days,
        "sheetTotals": totals,
        "footnotes": {n: v for n, v in footnotes.items() if v},
        "warnings": warnings,
        "statusDefs": status_defs,
    }


def resolve_month(m: dict, statuses: dict[str, str]) -> None:
    """Doplní časy směn z legendy (při víc možnostech rozhodne barva buňky), stavy, hodiny a varování."""
    month = m["month"]
    hours = {p: 0 for p in m["people"]}
    counts = {p: 0 for p in m["people"]}
    unknown: dict[str, list[str]] = {}
    ambiguous: dict[str, list[str]] = {}
    for day in m["days"]:
        own, other = ("weekend", "weekday") if day["weekend"] else ("weekday", "weekend")
        for s in day["shifts"]:
            n = to_num(s["raw"])
            s.update(hours=n, start=None, end=None, options=None)
            where = f"{s['person']} {day['day']}. {month}."
            if n is not None:
                hours[s["person"]] += n
                counts[s["person"]] += 1
                cands: list[dict] = []
                for sec in (own, other):
                    cands = [e for e in m["legend"][sec] if e["code"] == str(n)]
                    if len(cands) > 1 and s.get("color"):
                        cands = [e for e in cands if e["color"] == s["color"]] or cands
                    if cands:
                        break
                if len(cands) == 1:
                    s["start"], s["end"] = cands[0]["start"], cands[0]["end"]
                elif cands:
                    s["options"] = [[e["start"], e["end"]] for e in cands]
                    ambiguous.setdefault(s["raw"], []).append(where)
            if not s["start"] and not s["options"]:
                unknown.setdefault(s["raw"], []).append(where)
        day["statuses"] = [{"person": x["person"], "label": statuses[x["color"]]}
                           for x in day.pop("marks", []) if x["color"] in statuses]
    for code, where in unknown.items():
        what = "Kód" if to_num(code) is not None else "Hodnota"
        more = f" a další ({len(where) - 4})" if len(where) > 4 else ""
        m["warnings"].append(f"{what} „{code}“ není v legendě, čas začátku neznám ({'; '.join(where[:4])}{more})")
    for code, where in ambiguous.items():
        more = f" a další ({len(where) - 4})" if len(where) > 4 else ""
        m["warnings"].append(f"Kód „{code}“ má v legendě víc časů a barva buňky to nerozhodla ({'; '.join(where[:4])}{more})")
    for name, t in m["sheetTotals"].items():
        if t is not None and abs(hours[name] - t) > 0.01:
            m["warnings"].append(f"{name}: směny dávají {fmt_num(hours[name])} h, v řádku součtů je {fmt_num(t)} h.")
    m.update(hours=hours, shiftCount=counts)


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


def team_configs(cfg: dict) -> dict[str, dict]:
    return cfg.get("teams") or {cfg["team"]: {"default_person": cfg.get("default_person")}}


def load_all(cfg: dict) -> dict:
    """Všechny týmy najednou: stavové barvy jednoho týmu (např. oranžová = DENNÍ) se používají i u druhého."""
    sid = cfg["sheet_id"]
    tabs = list_tabs(sid)
    teams: dict[str, dict] = {}
    statuses: dict[str, str] = {}
    for team, tcfg in team_configs(cfg).items():
        months = []
        for t in tabs:
            ym = month_of_tab(t["name"], team)
            if ym:
                m = parse_month(fetch_rows(sid, t["gid"]), *ym, colors=fetch_colors(sid, t["gid"]))
                statuses.update(m.pop("statusDefs"))
                m.update(tab=t["name"], gid=t["gid"])
                months.append(m)
        months.sort(key=lambda m: m["key"])
        people: list[str] = []
        for m in months:
            people += [p for p in m["people"] if p not in people]
        teams[team] = {"slug": tcfg.get("slug") or slug(team), "defaultPerson": tcfg.get("default_person"),
                       "people": people, "months": months}
    for t in teams.values():
        for m in t["months"]:
            resolve_month(m, statuses)

    vacations: dict[str, list[str]] = {}
    vac_tab = next((t for t in tabs if fold(t["name"]) == fold(cfg.get("vacations_tab"))), None)
    if vac_tab:
        everyone = {fold(p): p for t in teams.values() for p in t["people"]}
        for name, vals in parse_vacations(fetch_rows(sid, vac_tab["gid"])).items():
            if fold(name) in everyone:
                vacations[everyone[fold(name)]] = vals

    return {
        "generated": dt.datetime.now().astimezone().isoformat(timespec="minutes"),
        "sheetUrl": f"https://docs.google.com/spreadsheets/d/{sid}/edit",
        "teams": teams,
        "vacations": vacations,
    }


def team_view(data: dict, query: str | None, default: str | None) -> dict:
    """Data jednoho týmu ve tvaru, se kterým pracují příkazy i šablona."""
    teams = data["teams"]
    q = fold(query or default or next(iter(teams)))
    name = next((n for n, t in teams.items() if q in (fold(n), t["slug"])), None)
    if name is None:
        raise SystemExit(f"Tým „{query}“ neznám. K dispozici: {', '.join(t['slug'] for t in teams.values())}.")
    t = teams[name]
    return {
        "generated": data["generated"],
        "team": name,
        "slug": t["slug"],
        "teams": [{"name": n, "slug": x["slug"]} for n, x in teams.items()],
        "sheetUrl": data["sheetUrl"],
        "defaultPerson": t["defaultPerson"],
        "people": t["people"],
        "months": t["months"],
        "vacations": {p: v for p, v in data["vacations"].items() if p in t["people"]},
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
    if not q:
        raise SystemExit(f"Tým {data['team']} nemá výchozí osobu, zadej --person. Lidé: {', '.join(people)}.")
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
        print(f"  {s['person']:<20} {fmt_shift(s)}  (kód {s['raw']})")
    for x in day.get("statuses", []):
        print(f"  {x['person']:<20} {x['label']}")
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


ROBOTS = '<meta charset="utf-8">\n<meta name="robots" content="noindex,nofollow">'


def render_html(data: dict, standalone: bool, public: bool) -> str:
    if public:
        # Veřejná stránka: bez odkazu na zdrojovou tabulku (obsahuje i další listy) a bez plánovaných dovolených.
        data = {**data, "public": True, "sheetUrl": "", "vacations": {}}
        standalone = True
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    html = TEMPLATE_PATH.read_text(encoding="utf-8").replace("__SMENY_DATA__", payload, 1)
    if standalone:
        head = STANDALONE_HEAD.replace('<meta charset="utf-8">', ROBOTS) if public else STANDALONE_HEAD
        html = head + html + "\n</body>\n</html>\n"
    return html


def write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def cmd_build(data, args):
    if args.person:
        data["defaultPerson"] = find_person(data, args.person)
    out = pathlib.Path(args.out)
    write(out, render_html(data, args.standalone, args.public))
    print(f"{out} – tým {data['team']}, {len(data['months'])} měs., data z {data['generated']}")


INDEX_HTML = """<!doctype html>
<html lang="cs">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Směny</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Barlow+Condensed:wght@600;700&family=Barlow:wght@400;500&display=swap">
<style>
:root{--bg:#EEF1F5;--surface:#FFFFFF;--ink:#121620;--muted:#596274;--line:#D8DDE5;--accent:#2E58E0;
--f-display:"Barlow Condensed","Arial Narrow",sans-serif;--f-body:"Barlow",system-ui,sans-serif}
:root[data-theme=dark]{--bg:#05070A;--surface:#0F131A;--ink:#F6F8FB;--muted:#B7C0CE;--line:#3A4455;--accent:#7F9DFF;color-scheme:dark}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--f-body)}
main{max-width:720px;margin:0 auto;padding:40px 16px;display:flex;flex-direction:column;gap:20px}
p{margin:0}.eyebrow{font-family:var(--f-display);font-weight:600;font-size:13px;letter-spacing:.14em;text-transform:uppercase;color:var(--muted)}
h1{font-family:var(--f-display);font-weight:700;font-size:52px;line-height:.9;text-transform:uppercase;margin:4px 0 0}
.teams{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:12px}
a.team{display:flex;flex-direction:column;gap:6px;padding:20px;border:1px solid var(--line);border-radius:10px;background:var(--surface);color:var(--ink);text-decoration:none}
a.team:hover,a.team:focus-visible{border-color:var(--accent);outline:none}
a.team b{font-family:var(--f-display);font-weight:700;font-size:36px;line-height:1;text-transform:uppercase}
a.team span{color:var(--muted);font-size:14px}
</style>
<script>try{if(localStorage.getItem('smeny.theme')==='dark')document.documentElement.dataset.theme='dark'}catch(e){}</script>
</head>
<body>
<main>
<div><p class="eyebrow">Rozpis směn · grafika</p><h1>Směny</h1></div>
<div class="teams">__TEAMS__</div>
</main>
</body>
</html>
"""


def cmd_site(data_all, args):
    out = pathlib.Path(args.out)
    cards = []
    for name, t in data_all["teams"].items():
        view = team_view(data_all, t["slug"], None)
        write(out / t["slug"] / "index.html", render_html(view, True, True))
        months = ", ".join(m["label"] for m in view["months"]) or "zatím bez rozpisu"
        cards.append(f'<a class="team" href="./{t["slug"]}/"><b>{name}</b>'
                     f'<span>{len(view["people"])} lidí · {months}</span></a>')
        print(f"{out / t['slug'] / 'index.html'} – tým {name}, {len(view['months'])} měs.")
    write(out / "index.html", INDEX_HTML.replace("__TEAMS__", "".join(cards)))
    print(f"{out / 'index.html'} – rozcestník, data z {data_all['generated']}")


def cmd_check(data, args):
    print(f"Tým {data['team']}, lidé: {', '.join(data['people'])}")
    for m in data["months"]:
        print(f"\n{m['label']} (list „{m['tab']}“): {len(m['days'])} dnů, "
              + ", ".join(f"{p} {fmt_num(h)} h" for p, h in m["hours"].items()))
        print("  legenda: " + "; ".join(f"{sec} {e['code']} = {fmt_time(e['start'])}–{fmt_time(e['end'])}"
                                       + (f" ({e['color']})" if e["color"] else "")
                                       for sec, entries in m["legend"].items() for e in entries))
        for w in m["warnings"] or ["bez problémů"]:
            print(f"  ! {w}" if m["warnings"] else f"  {w}")
    if data["vacations"]:
        print("\nPlánované dovolené: " + "; ".join(f"{p}: {', '.join(v)}" for p, v in data["vacations"].items()))


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Směny z Google tabulky")
    ap.add_argument("--team", help="tým: sport, denni (výchozí podle config.json)")
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
    p = sub.add_parser("site"); p.add_argument("--out", required=True)
    p = sub.add_parser("set-artifact"); p.add_argument("url")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "set-artifact":
        cfg["artifact_url"] = args.url
        save_config(cfg)
        print(f"Uloženo: {args.url}")
        return
    data_all = load_all(cfg)
    if args.cmd == "site":
        cmd_site(data_all, args)
        return
    data = team_view(data_all, args.team, cfg.get("team"))
    {"next": cmd_next, "day": cmd_day, "month": cmd_month, "json": cmd_json, "ics": cmd_ics,
     "build": cmd_build, "check": cmd_check}[args.cmd](data, args)


if __name__ == "__main__":
    main()
