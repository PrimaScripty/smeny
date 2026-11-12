# Směny

Přehled směn týmů SPORT a DENNÍ z Google tabulky: další směna, měsíční kalendář, časová osa, hodiny a kontrola rozpisu. Je to také Claude skill `/smeny` (viz [SKILL.md](SKILL.md)).

## Jak to funguje

- `scripts/smeny.py` stáhne tabulku přes veřejný CSV export, najde listy `<TÝM> <měsíc> <rok>` a z legendy pod rozpisem určí časy směn. Když má kód víc časů, rozhodne barva buňky z HTML náhledu listu. Používá jen standardní knihovnu Pythonu 3.
- Ruční časy směn, které tabulka nemá (třeba noční směna o volbách USA), jsou v proměnné `SMENY_OVERRIDES`, lokálně v `overrides.json`. Vzor je [overrides.example.json](overrides.example.json).
- `assets/wrapper.html` je šablona přehledu. Data se do ní vloží při sestavení.
- GitHub Actions ([pages.yml](.github/workflows/pages.yml)) web (rozcestník, `/sport/`, `/denni/`) každých 30 minut sestaví znovu a nasadí na GitHub Pages.

Repozitář je veřejný, takže v něm nejsou ID tabulky ani jména lidí. Jména jsou jen v Google tabulce, v secretech GitHub Actions a v lokálních souborech `config.json` a `overrides.json` (oba v `.gitignore`).

## Nastavení repozitáře

1. Settings → Pages → Source: GitHub Actions.
2. Settings → Secrets and variables → Actions:

| Název | Kde | Hodnota |
|---|---|---|
| `SMENY_SHEET_ID` | Variables nebo Secrets | ID Google tabulky (část URL mezi `/d/` a `/edit`) |
| `SMENY_PERSON` | Secrets | výchozí osoba týmu SPORT (celé jméno jako v tabulce). Stránka `/sport/` ji vybere, dokud si návštěvník nezvolí jiného. Bez ní se vybere první člověk v tabulce. |
| `SMENY_OVERRIDES` | Secrets | ruční časy směn jako JSON na jeden řádek, viz níže. Bez něj se zvláštní směny ukážou jen s délkou nebo názvem akce. |

Jména patří jen do **Secrets**. Hodnoty z Variables vypíše GitHub čitelně do logu každého běhu a log veřejného repozitáře si přečte každý přihlášený uživatel GitHubu. Secrets se v logu ukážou jako `***`.

Veřejná verze nemá odkaz na zdrojovou tabulku ani plánované dovolené a je označená `noindex`.

### Ruční časy směn

Jeden záznam na směnu: `team` (SPORT nebo DENNÍ), `date` (`RRRR-MM-DD`), `person` (jméno jako v tabulce, na diakritice a velikosti písmen nezáleží), `start` a `end` (`HH:MM`, konec po půlnoci je další den), volitelně `approx: true` (zobrazí se „cca“) a `why` (jen poznámka). Ruční čas má přednost před legendou tabulky.

1. Lokálně uprav `overrides.json` (napoprvé `copy overrides.example.json overrides.json`).
2. `python scripts/smeny.py overrides` soubor zkontroluje a vypíše ho na jeden řádek.
3. Ten řádek vlož do Settings → Secrets and variables → Actions → Secrets → `SMENY_OVERRIDES` → Update. Secret je potřeba přepsat celý, zpátky ho přečíst nejde.
4. Actions → Obnovit přehled směn → Run workflow, nebo počkat na další běh. V logu kroku „Postavit přehled z tabulky“ je vidět počet načtených ručních časů. Varování ukáže vadný záznam a záznam, který nesedí na žádnou směnu (třeba překlep ve jménu).

Víceřádkový JSON skript přečte taky, ale GitHub by pak v logu maskoval každý jeho řádek zvlášť, i samotné závorky.

## Lokálně

```
copy config.example.json config.json    (a doplň sheet_id a výchozí osobu default_person)
python scripts/smeny.py next
python scripts/smeny.py build --standalone --out out/smeny-sport.html
```

Na Windows stačí dvojklik na `smeny.cmd`. Proměnné prostředí `SMENY_SHEET_ID`, `SMENY_PERSON` a `SMENY_OVERRIDES` mají přednost před `config.json` a `overrides.json`.
