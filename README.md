# Směny

Přehled směn týmu SPORT z Google tabulky: další směna, měsíční kalendář, časová osa, hodiny a kontrola rozpisu. Je to také Claude skill `/smeny` (viz [SKILL.md](SKILL.md)).

## Jak to funguje

- `scripts/smeny.py` stáhne tabulku přes veřejný CSV export, najde listy `SPORT <měsíc> <rok>` a z legendy pod rozpisem určí časy směn. Používá jen standardní knihovnu Pythonu 3.
- `assets/wrapper.html` je šablona přehledu. Data se do ní vloží při sestavení.
- GitHub Actions ([pages.yml](.github/workflows/pages.yml)) přehled každých 30 minut sestaví znovu a nasadí na GitHub Pages.

## Nastavení repozitáře

1. Settings → Pages → Source: GitHub Actions.
2. Settings → Secrets and variables → Actions → Variables: `SMENY_SHEET_ID` = ID Google tabulky (část URL mezi `/d/` a `/edit`).

ID tabulky v repozitáři není. Veřejná verze nemá odkaz na zdrojovou tabulku ani plánované dovolené a je označená `noindex`.

## Lokálně

```
copy config.example.json config.json    (a doplň sheet_id)
python scripts/smeny.py next
python scripts/smeny.py build --standalone --out out/smeny-sport.html
```

Na Windows stačí dvojklik na `smeny.cmd`.
