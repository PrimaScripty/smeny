---
name: smeny
description: Rozpis směn týmů SPORT a DENNÍ (grafika Prima Sport) z Google tabulky. Kdy má Martin Tomek (nebo kdokoli z týmu) další směnu, kdo dělá v určitý den, kolik hodin má v měsíci, export směn do Google Kalendáře (.ics) a obnova přehledu „Směny SPORT“ (Artifact). Použij, když se uživatel ptá na směny, službu, rozpis, „kdy jdu do práce“, „mám volno?“, „kdo je v sobotu“, „hodiny v listopadu“, „dej směny do kalendáře“, „obnov přehled směn“, nebo napíše /smeny.
---

# Směny SPORT a DENNÍ

Zdroj je Google tabulka (`sheet_id` v `config.json`), veřejně čitelná přes CSV export, takže není potřeba přihlášení.
Měsíce jsou na listech `<TÝM> <měsíc> <rok>` (např. „SPORT listopad 2026“, „DENNÍ říjen 2026“) a skript nové měsíce najde sám. Týmy jsou v `config.json` pod `teams`.
Výchozí tým je SPORT (`team`) a výchozí osoba **Martin Tomek**. DENNÍ výchozí osobu nemá. Když uživatel říká „já“ nebo „moje směny“, myslí tím ji, pokud neřekne jinak.

## Jak číst tabulku

- Hlavička tvoří 2 řádky: jméno a příjmení. Pod nimi je jeden řádek na každý den (číslo dne, den v týdnu, buňka pro každého člověka). Za lidmi je sloupec se součtem dne a poznámka (např. „Zaučení AL“).
- **Číslo v buňce je počet hodin.** Čas začátku a konce je v legendě pod rozpisem. Teď platí: všední dny `8` = 6:00–14:00, `7` = 13:00–20:00, `14` = 6:00–20:00, víkend `12` = 6:00–18:00. Skript legendu čte z listu, takže změny pozná sám.
- V DENNÍ má stejný kód víc časů (`7` = 12–19 nebo 15–22, `10` = 8–18 nebo 9–19). Rozhoduje **barva buňky**, která odpovídá barvě kódu v legendě, a má přednost i před tím, jestli je všední den, nebo víkend: o svátcích se všední den zapisuje víkendovými barvami (žlutá `8` = 14–22). Skript barvy čte z HTML náhledu listu. Když barva nerozhodne, výstup ukáže obě možnosti. Neodhaduj, řekni to tak.
- Zvláštní akce: když barva směny v legendě není, ale stejnou barvu má poznámka dne (např. „volby USA“ světle modře), patří směna k té akci a ukáže se jako „10 h, volby USA“. Čas v tabulce není.
- Časy, které v tabulce nejsou, se doplňují ručně do `overrides.json` v kořeni repozitáře (tým, datum, člověk, `start`, `end`, `approx` pro přibližný čas, `why`). Ruční úprava má přednost před legendou. Když uživatel řekne čas zvláštní směny, přidej záznam tam. Přibližný čas se zobrazí jako „cca“. Soubor je veřejný v repozitáři, takže do `why` patří jen to, co už je na webu.
- Prázdné barevné buňky jsou stavy z legendy: červená = vyblokováno, tyrkysová = dělá SPORT, žlutá nebo oranžová = dělá DENNÍ.
- Rok bere skript z názvu listu, ale ověří ho podle dnů v týdnu. List zkopírovaný ze šablony se starým rokem zařadí podle skutečného roku a upozorní na to v kontrole.
- Kódy, které v legendě nejsou (např. `3`, `13`), mají jen délku, ne čas. Řekni to tak a čas si nevymýšlej.
- Pod rozpisem jsou poznámky k lidem (např. „3 školení Piero“, „1 AI 1 Laver Cup 1 NHL“) a řádek součtů hodin.
- Pod rozpisem DENNÍ jsou i číselné řádky (online, částky). Skript z nich nebere nic kromě řádku součtů hodin a nikam je nepropisuj.
- „Martin“ v DENNÍ je Martin Málek, ne Martin Tomek. Jan Smutný je v obou týmech.

## Skript

Vždy ho spusť znovu, rozpis se mění. Nikdy neodpovídej z paměti ani z dřívějšího výstupu.

```bash
python ~/.claude/skills/smeny/scripts/smeny.py [--team sport|denni] <příkaz>
```

Bez `--team` platí SPORT. Když se ptá na někoho z DENNÍ, přidej `--team denni`.

| Příkaz | K čemu |
|---|---|
| `next [--person JMÉNO] [-n 5]` | nejbližší směny včetně té, která právě probíhá, a kdo má směnu ve stejný den |
| `day DATUM` | kdo dělá v daný den (`dnes`, `zítra`, `pátek`, `23.10.`, `2026-10-23`) |
| `month [MĚSÍC] [--person JMÉNO]` | celý měsíc (`2026-11`, `listopad`, `11`), s osobou jen její směny a součet hodin |
| `check` | kontrola tabulky: neexistující dny, nesedící součty, kódy bez legendy |
| `json [--out SOUBOR]` | všechna data, když potřebuješ něco, co ostatní příkazy nevypíšou |
| `ics [--person JMÉNO] [--since DATUM] --out SOUBOR` | export do kalendáře |
| `build --out SOUBOR [--person JMÉNO]` | HTML přehled (šablona `assets/wrapper.html`) |
| `site --out SLOŽKA` | veřejný web: rozcestník, `sport/` a `denni/` (to dělá GitHub Actions) |
| `set-artifact URL` | uloží URL publikovaného přehledu do `config.json` |

Jména se hledají bez ohledu na diakritiku a stačí jednoznačná část (`tomek`, `bláha`, `alex`).

## Odpovídání

- Česky a krátce. U data uveď den v týdnu (`po 19. 10.`), čas (`6:00–14:00`) a s kým se člověk ten den střídá.
- Relativní výrazy („zítra“, „za 5 dní“, „probíhá“) počítej od aktuálního data a času.
- Když se otázka týká dne, pro který zatím není list, řekni, že rozpis sahá jen do posledního měsíce v tabulce.
- Když `check` nebo výstup ukáže varování, které souvisí s otázkou (třeba nesedící součty), zmiň ho jednou větou.

## Google Kalendář

1. Když je v session dostupný nástroj pro Google Kalendář (konektor), nejdřív si v cílovém období najdi existující události „Směna SPORT …“, ať nevznikají duplikáty. Před zápisem uživateli ukaž seznam a nech si ho potvrdit. Teprve pak události vytvoř nebo uprav.
2. Jinak spusť `ics --out <soubor>.ics` do pracovní složky (výchozí od dneška pro výchozí osobu) a vysvětli import: Google Kalendář na webu → ozubené kolo → Nastavení → Import a export → Import, vybrat soubor a kalendář (ideálně samostatný kalendář „Směny“).
   Každá událost má stabilní UID (`datum-osoba-tym@smeny`), takže opakovaný import by neměl vytvářet duplikáty. Smazané směny ale import neodstraní, ty se musí smazat ručně.

## Obnova přehledu (Artifact „Směny SPORT“)

Přehled má data vložená ve stránce. Sám z Googlu stahovat nemůže, protože to Artifact nedovolí. Odpočet a aktuální čas ale počítá živě.

1. `python ~/.claude/skills/smeny/scripts/smeny.py build --out <scratchpad nebo pracovní složka>/smeny-sport.html`
2. Přečti `artifact_url` z `~/.claude/skills/smeny/config.json`.
   - **URL je:** nejdřív Artifact `action: "read"` s tou URL (bez toho se publikování odmítne), potom Artifact publish s `file_path` vygenerovaného souboru a `url`. `icon` nedávej.
   - **URL je `null`:** publikuj nový Artifact s `icon: "calendar"` a URL ulož příkazem `set-artifact`.
3. Uživateli pošli odkaz a jednou větou napiš, co se od minula změnilo (podle `check` a `next`).

Pro Artifact **nepoužívej** `--standalone`, protože Artifact si kostru stránky přidává sám.

## Lokální HTML

`smeny.cmd` ve složce skillu stáhne čerstvá data, vyrobí `out/smeny-sport.html` (`build --standalone`, tedy celý dokument s `<!doctype>` a kódováním) a otevře ho v prohlížeči. Uživatel ho spouští dvojklikem, Claude k tomu není potřeba.
Když uživatel chce „otevřít přehled u sebe“, spusť `smeny.cmd`. Soubor otevřený přímo z disku si data z Googlu stáhnout nemůže, protože prohlížeč to u `file://` blokuje. Proto se data obnovují tím, že se soubor vyrobí znovu.

## Veřejný web (GitHub Pages)

Repozitář je na GitHubu u účtu `PrimaScripty`, web má rozcestník a stránky `/sport/` a `/denni/` (adresu najdeš v Settings → Pages). GitHub Actions ([pages.yml](.github/workflows/pages.yml)) web každých 30 minut a při každém pushi postaví znovu příkazem `site`. Claude k tomu není potřeba.

- Číslo tabulky je v repozitáři jen jako proměnná `SMENY_SHEET_ID` (Settings → Secrets and variables → Actions → Variables). V `config.json` je lokálně, soubor je v `.gitignore`. Nikdy ho nedávej do commitu ani do veřejného výstupu.
- Veřejné stránky (`--public`, `site`) vynechají odkaz na zdrojovou tabulku a plánované dovolené a přidá `noindex`. Na webu jsou jména a směny lidí z týmu, takže před změnou toho, co se zveřejňuje, se zeptej.
- Změna kódu nebo šablony se na web dostane pushem na `main`. Push i další zápisy na GitHub si uživatel dělá sám v Terminálu, systém je Claudovi blokuje. Stav sestavení jde číst bez tokenu: `https://api.github.com/repos/PrimaScripty/smeny/actions/runs`.
- GitHub vypne plánovaná spuštění po 60 dnech bez aktivity v repozitáři. Pak je potřeba workflow znovu zapnout v záložce Actions.

Šablonu přehledu upravuj v `assets/wrapper.html`. Data se vkládají místo zástupného textu `__SMENY_DATA__`, který v šabloně musí zůstat přesně jednou.
