# JobAgent — lokal jobbsökning

Lokal, manuell sökning i Platsbanken med Python 3.12, SQLite och ett CLI.
Ingen AI-server eller modell krävs för sökning och lagring. Docker, webbläsarautomation
och betald API-nyckel behövs inte. Ett separat `analyze`-kommando kan använda LM Studio.
`upstream-reference` är en orörd referens; implementationen använder inte dess moduler.

## Kör i PowerShell

Miljön har skapats lokalt i `.venv`. Aktivering behövs inte:

```powershell
Set-Location E:\AI\JobAgent
.\.venv\Scripts\python.exe -m jobagent config-check
.\.venv\Scripts\python.exe -m jobagent search --query Java --max-pages 1
.\.venv\Scripts\python.exe -m jobagent list --limit 20
.\.venv\Scripts\python.exe -m jobagent show 1
.\.venv\Scripts\python.exe -m jobagent history
```

`search` utan argument använder alla sökord i `config/settings.toml`. Flera sökningar:

```powershell
.\.venv\Scripts\python.exe -m jobagent --verbose search --query Java --query "Spring Boot" --days 14
```

Resultat skrivs som läsbar JSON till stdout. `--verbose` skriver JSON-progressloggar till stderr,
utan annonstext eller profilinnehåll. `jobagent.exe` finns också i `.venv\Scripts`.
Globala flaggor (`--root`, `--config`, `--verbose`) placeras före kommandot.
Från en annan arbetskatalog: ange `--root E:\AI\JobAgent`.

Exitkoder: `0` = komplett/lyckad körning, `1` = fel, `2` = delvis hämtade resultat,
`130` = avbruten körning. Delvis hämtning betyder inte att sparade annonser förlorats.

## Installation från början

```powershell
Set-Location E:\AI\JobAgent
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip --cache-dir E:\AI\JobAgent\.cache\pip install -r requirements.lock
.\.venv\Scripts\python.exe -m pip --cache-dir E:\AI\JobAgent\.cache\pip install --no-deps --no-build-isolation -e .
.\.venv\Scripts\python.exe -m pytest -q
```

`requirements.lock` låser den verifierade Windows/Python 3.12-miljön inklusive testverktyg.
Den lokala miljön, databasen och paketcache ligger på E:. Python/Windows kan fortfarande
använda systemets temporära katalog på C: under installation.

## Konfiguration

`config/settings.toml` innehåller databasplats, API-inställningar, sökord, antal dagar
och sidgränser. Relativa databasvägar räknas alltid från projektroten.
API-adressen måste vara HTTPS. Okända konfigurationsfält avvisas.

Miljövariabler kan överstyra `JOBAGENT_DATABASE`, `JOBAGENT_API_BASE_URL` och
`JOBAGENT_MINIMUM_SCORE`. Exempel:

```powershell
$env:JOBAGENT_DATABASE = 'E:\AI\JobAgent\var\experiment.sqlite3'
.\.venv\Scripts\python.exe -m jobagent search --query testutvecklare
Remove-Item Env:JOBAGENT_DATABASE
```

Matchningsgränsen 70 lagras för framtida AI-analys och filtrerar inte annonser i fas 1.
Profilen behövs inte för hämtning. Den kan valideras separat:

```powershell
.\.venv\Scripts\python.exe -m jobagent config-check --profile config/profile.example.toml
```

Exempelprofilen anger cirka två års deltidsarbete, 157 högskolepoäng och ingen utfärdad
examen. Den anger också fullstackerfarenhet och REST API-utveckling. Egna profilfiler
kan läggas i den Git-ignorerade katalogen `private`.

## Lokal analys av en annons

Starta LM Studios lokala API-server och ladda `openai/gpt-oss-20b`. Kör sedan:

```powershell
.\.venv\Scripts\python.exe -m jobagent analyze 188
# Egen profil (sökvägen räknas från projektroten):
.\.venv\Scripts\python.exe -m jobagent analyze 188 --profile private/profile.toml
```

Standardprofilen är `config/profile.example.toml`; använd en egen uppdaterad profil
för din personliga bedömning. Äldre profiler utan `roles` och `experience_details`
kan fortfarande läsas, men dessa fakta kan då saknas i jämförelsen.

Kommandot läser endast den befintliga databasen och skriver resultatet till terminalen.
Det skapar inga analystabeller, beräknar ingen matchningspoäng och skickar inga ansökningar.
Resultatet innehåller svensk sammanfattning, krav, kvalifikationsalternativ, jämförelser,
osäkerheter och modell-/prompt-/profilproveniens. Annonsversion och innehållshash anges.

`[llm]` i `config/settings.toml` styr adress, modell, timeout, tokenbudget och indatagräns.
`JOBAGENT_LLM_BASE_URL` och `JOBAGENT_LLM_MODEL` överstyr adress/modell. Adressen är
`http://127.0.0.1:1234/v1` som standard. Endast loopback-adresser accepteras i denna version;
ingen proxy, omdirigering eller automatisk molnreserv används. Bara `analyze` kontaktar AI.

Svar måste följa JSON-schemat. Citat ska finnas ordagrant i annonsen. Python delar
ordagranna "eller/och/eller"-uttryck i alternativ för visning, i stället för att lita på
modellens alternativlistor. Hela citatet visas alltid: en uppdelning inom parentes
ersätter inte gemensamma krav utanför parentesen. Detta är textuppdelning, inte ett
beslut om behörighet. Avklippta alternativ och ofullständiga svar avvisas med exitkod 1.
Felmeddelanden innehåller inte profil eller råa modellsvar. Återförsök görs manuellt.
För stor indata avvisas före anrop; inget klipps tyst. Teckengränsen är en grov gräns,
inte en garanti för att modellens kontext räcker. Vid kontextfel behöver modellens
kontextstorlek/utdatabudget justeras.

**Citatkontroll bevisar inte att modellens tolkning är korrekt.** Kravnivå, fullständighet
och jämförelser ska granskas av dig. Prompten skiljer på förväntade och uttryckliga krav,
bevarar utbildnings-/certifieringsalternativ och förbjuder att deltidsår automatiskt
räknas om till heltid. Den garanterar inte semantisk korrekthet. Om modellen bedömer
ett uttryckligt avancerat nivåkrav som styrkt med enbart kompetenslistan, begränsar
Python bedömningen till delvis stöd och visar en granskningsnotering. Detta är en
konservativ kontroll för vissa nivåuttryck, inte en fullständig bedömningsmotor.

För korrekt UTF-8-utmatning i äldre PowerShell eller vid omdirigering kan du ange:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m jobagent analyze 188
```

API-formatet följer [LM Studios dokumentation om strukturerade svar](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

## Lagring och avgränsningar

Databasen skapas vid första databaskommandot; `config-check` skapar inga databasfiler.
Tre tabeller används: `jobs`, `ad_versions`, `search_runs`. Schema 1 registreras med
SQLite `user_version`. Annonser identifieras med `(source, external_id)`. Samma annons
uppdateras i stället för att dupliceras; ändrat normaliserat innehåll skapar en version.
Varje version bevarar rå JSON. Enbart förändringar i rå sökrankning skapar ingen version.
Titel och arbetsgivare ensamma används aldrig för att slå ihop annonser.

Varje validerad sida sparas i en kort transaktion. WAL, främmande nycklar och en
låsväntetid är aktiverade. API-anrop görs utanför skrivtransaktioner. Upprepade fynd
räknas som `unchanged`; det är ett antal fynd, inte unika annonser över alla sökord.

Hämtningen använder en fast publiceringsgräns per körning och söker alla platser.
JobTech tillåter högst 100 annonser per sida och offset 2000. För breda sökningar markeras
som `partial`; automatisk uppdelning av datumfönster ingår inte i denna lilla version.
Minska `--days` eller använd snävare sökord. Ett slutligt antal är en ögonblicksbild;
API-resultat kan ändras under paginering. Upprepa gärna sökningar med överlapp.

API-tider utan offset tolkas enligt `source.timezone` (standard Europe/Stockholm);
detta är ett uttryckligt, ändringsbart antagande. Normaliserade tider sparas som UTC,
och originalvärdena bevaras i rådata. Distans/hybrid anges som okänt tills säkra uppgifter
kan tolkas. Beskrivningar behandlas som data och körs aldrig som instruktioner.

En annons som återkommer med ändringar uppdateras. Annonser som inte återkommer märks
inte automatiskt som borttagna. Fullständig uppdaterings-/borttagningssynk, separata
annonsuppslag, korskälla-deduplicering, dashboard, AI, favoriter och ansökningsspårning
är senare steg (utöver det separata analysprovet ovan). Om processen tvångsavslutas kan en sökkörning stå kvar som `running`;
redan sparade sidor finns kvar. Ingen bakgrundstjänst startas.

## Tester och källor

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Automatiska tester använder HTTP-mockar och tillfälliga databaser. De anropar varken
JobTech eller LM Studio och läser inte referensprojektets databas.

- [JobTech API och Swagger](https://jobsearch.api.jobtechdev.se/)
- [API-specifikation](https://jobsearch.api.jobtechdev.se/swagger.json)

API-specifikationen anger CC0 för annonserna. Personuppgifter i annonser bör ändå
hanteras varsamt. Inga filer, promptar eller mallar har kopierats från GPL-referensen.
Eventuell framtida kodåteranvändning därifrån kräver separat licensbedömning.
