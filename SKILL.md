---
name: smeny
description: Rozpis směn týmu SPORT (grafika Prima Sport) z Google tabulky. Kdy má Martin Tomek (nebo kdokoli z týmu) další směnu, kdo dělá v určitý den, kolik hodin má v měsíci, export směn do Google Kalendáře (.ics) a obnova přehledu „Směny SPORT“ (Artifact). Použij, když se uživatel ptá na směny, službu, rozpis, „kdy jdu do práce“, „mám volno?“, „kdo je v sobotu“, „hodiny v listopadu“, „dej směny do kalendáře“, „obnov přehled směn“, nebo napíše /smeny.
---

# Směny SPORT

Zdroj je Google tabulka (`sheet_id` v `config.json`), veřejně čitelná přes CSV export, takže není potřeba přihlášení.
Měsíce jsou na listech `SPORT <měsíc> <rok>` (např. „SPORT listopad 2026“) a skript nové měsíce najde sám.
Ve výchozím nastavení je osoba **Martin Tomek** (`default_person`). Když uživatel říká „já“ nebo „moje směny“, myslí tím ji, pokud neřekne jinak.

## Jak číst tabulku

- Hlavička tvoří 2 řádky: jméno a příjmení. Pod nimi je jeden řádek na každý den (číslo dne, den v týdnu, buňka pro každého člověka). Za lidmi je sloupec se součtem dne a poznámka (např. „Zaučení AL“).
- **Číslo v buňce je počet hodin.** Čas začátku a konce je v legendě pod rozpisem. Teď platí: všední dny `8` = 6:00–14:00, `7` = 13:00–20:00, `14` = 6:00–20:00, víkend `12` = 6:00–18:00. Skript legendu čte z listu, takže změny pozná sám.
- Kódy, které v legendě nejsou (např. `3`, `13`), mají jen délku, ne čas. Řekni to tak a čas si nevymýšlej.
- Pod rozpisem jsou poznámky k lidem (např. „3 školení Piero“, „1 AI 1 Laver Cup 1 NHL“) a řádek součtů hodin.
- Listy `DENNÍ …` patří jinému týmu se sporou legendou (stejný kód tam znamená víc časů). Skill je nepodporuje. „Martin“ v listech DENNÍ je Martin Málek, ne Martin Tomek.

## Skript

Vždy ho spusť znovu, rozpis se mění. Nikdy neodpovídej z paměti ani z dřívějšího výstupu.

```bash
python ~/.claude/skills/smeny/scripts/smeny.py <příkaz>
```

| Příkaz | K čemu |
|---|---|
| `next [--person JMÉNO] [-n 5]` | nejbližší směny včetně té, která právě probíhá, a kdo má směnu ve stejný den |
| `day DATUM` | kdo dělá v daný den (`dnes`, `zítra`, `pátek`, `23.10.`, `2026-10-23`) |
| `month [MĚSÍC] [--person JMÉNO]` | celý měsíc (`2026-11`, `listopad`, `11`), s osobou jen její směny a součet hodin |
| `check` | kontrola tabulky: neexistující dny, nesedící součty, kódy bez legendy |
| `json [--out SOUBOR]` | všechna data, když potřebuješ něco, co ostatní příkazy nevypíšou |
| `ics [--person JMÉNO] [--since DATUM] --out SOUBOR` | export do kalendáře |
| `build --out SOUBOR [--person JMÉNO]` | HTML přehled (šablona `assets/wrapper.html`) |
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

Šablonu přehledu upravuj v `assets/wrapper.html`. Data se vkládají místo zástupného textu `__SMENY_DATA__`, který v šabloně musí zůstat přesně jednou.
