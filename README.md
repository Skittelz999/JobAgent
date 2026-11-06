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

Den äldre matchningsgränsen 70 behålls för konfigurationskompatibilitet. Den används
inte av insamlingen eller YAML-matchningen.
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
Resultatet innehåller svensk sammanfattning, krav, hela uttryck med kvalifikationsalternativ, jämförelser,
osäkerheter och modell-/prompt-/profilproveniens. Annonsversion och innehållshash anges.

`[llm]` i `config/settings.toml` styr adress, modell, timeout, tokenbudget och indatagräns.
`JOBAGENT_LLM_BASE_URL` och `JOBAGENT_LLM_MODEL` överstyr adress/modell. Adressen är
`http://127.0.0.1:1234/v1` som standard. Endast loopback-adresser accepteras i denna version;
ingen proxy, omdirigering eller automatisk molnreserv används. Bara `analyze` kontaktar AI.

Modellen väljer numrerade originalpassager genom ett JSON-schema. Python hämtar
citatet från originalet, kontrollerar referenserna och jämför mot dokumenterade
profiluppgifter. Felaktiga/dubbla referenser och ofullständiga svar avvisas med exitkod 1.
Sammanfattningens grupper och detaljerna byggs från samma kravlista. Modellen bestämmer
passagens typ, kravnivå, villkorlighet och kontexthänvisningar. Den får hela den ordnade
annonsen men ingen kandidatprofil och inga förutbestämda kravnivåer. Python behåller
modellens semantiska beslut och markerar dem för manuell granskning; ett giltigt citat
bevisar inte rätt tolkning. Profiljämförelsen körs därefter separat.
Alternativa uttryck redovisas med originalpassager och modellens kontexthänvisningar.
Deras räckvidd behöver granskas, särskilt när ett alternativ sträcker sig över flera passager.
Felmeddelanden innehåller inte profil eller råa modellsvar. Återförsök görs manuellt.
För stor indata avvisas före anrop; inget klipps tyst. Teckengränsen är en grov gräns,
inte en garanti för att modellens kontext räcker. Vid kontextfel behöver modellens
kontextstorlek/utdatabudget justeras.

**Giltiga källreferenser bevisar inte att alla relevanta krav identifierats.** Kravnivå,
fullständighet och jämförelser ska granskas av dig. Inga saknade krav fylls i automatiskt.
Villkorlighet är också en modellbedömning som behöver granskas.

Jämförelserna visar exakta profilreferenser och skiljer `supported` (uttryckligt stöd),
`partial` (delvis/relaterat stöd), `unknown` (otillräckliga uppgifter) och `not_met`
(känd avvikelse mot den angivna profilen). `not_met` innebär inget automatiskt
ansökningsbeslut. PostgreSQL ger relaterat stöd för SQL och JUnit/MockMvc/Testcontainers
för testkompetens; sådana kopplingar bevisar inte yrkesår eller specifika andra verktyg.
Spring Boot är inte bevis för Spring MVC-erfarenhet, och fullstackarbete bevisar inte
microservices. Deltidsår räknas inte om till heltidsår. Den svenska sammanfattningen
visar antal och originalcitat per kravgrupp, inte en separat fri modellbedömning.

För korrekt UTF-8-utmatning i äldre PowerShell eller vid omdirigering kan du ange:

```powershell
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m jobagent analyze 188
```

API-formatet följer [LM Studios dokumentation om strukturerade svar](https://lmstudio.ai/docs/developer/openai-compat/structured-output).

### YAML-baserad direktmatchning

```powershell
.\.venv\Scripts\python.exe -m jobagent config-check --profile config/profile.yaml
.\.venv\Scripts\python.exe -m jobagent match 188
.\.venv\Scripts\python.exe -m jobagent match 188 --profile private/profile.yaml
# ID här är resultatets result_id, inte annonsens ID:
.\.venv\Scripts\python.exe -m jobagent match-show 1
```

`match` använder `config/profile.yaml` som standard. Profilen innehåller den äldre
TOML-profilens fakta och användarens senare kompletteringar. Sektionerna är
`education`, `certifications`, `experience`, `skills`, `languages` och
`other_qualifications`. Certifieringar har `name` och `category`. Under `skills`
finns även `testing` och `methodologies`. Språk och övriga kvalifikationer
är listor med beskrivande text; körkort kan dokumenteras under övriga kvalifikationer.
YAML läses med `safe_load` och strukturen valideras med Pydantic. Saknade fält
läggs inte till i modellens indata. Tomma listor eller null betyder ospecificerat,
inte ett negativt besked. Dokumenterade arbetsperioder ligger i
`experience.documented`; `total_professional_years` lämnas ospecificerat tills
hela yrkesperioden är känd. Deltidsår räknas inte om till heltidsår.

Python skickar endast titel, fullständig beskrivning och den inlästa profilen
med en kort systemprompt i ett enda anrop till lokal GPT-OSS 20B via befintlig
LM Studio-transport. Modellen identifierar och jämför kraven. Python delar inte
upp annonstexten, jämför inte kompetenser och korrigerar inga modellbedömningar.
Ingen extra granskare, poäng, rangordning eller A/B/C-rekommendation används.

Modellens JSON-format är:

```json
{
  "requirements": [
    {
      "requirement": "Java",
      "type": "required",
      "status": "TRUE",
      "reason": "Java finns i profilen."
    }
  ]
}
```

`type` är `required`, `preferred` eller `practical`. `status` är `TRUE` (styrkt),
`PARTIAL` (delvis styrkt), `FALSE` (uttrycklig motsägelse) eller `UNKNOWN`
(otillräckliga uppgifter). Prompten ber modellen bevara alternativ och identifiera
även språk, körkort, närvaro, säkerhetsprövning och andra praktiska villkor.
Ett giltigt JSON-svar bevisar inte att modellen följt instruktionerna eller hittat
alla krav; även en tom kravlista är strukturellt möjlig. Semantiken kräver granskning.

Promptversion `yaml-match-v2` förtydligar att annonsen är enda källan till krav,
att hela kravet inklusive nivå måste styrkas för `TRUE`, och att `FALSE` kräver
en uttrycklig motsägelse. Korta exempel skiljer språknamn från flytande nivå och
dokumenterade deltidsår från total erfarenhet. Modellen uppmanas också att kontrollera
meriter och praktiska avsnitt före svaret. JSON-format och Python-validering är oförändrade.

Ett valfritt experiment med tre uttryckliga kravgrupper finns via `match 188 --grouped`.
Promptversionen är `yaml-match-groups-v1`. Modellens svar har tre obligatoriska
listor: `required`, `preferred` och `practical`. Varje post innehåller `requirement`,
`status` och `reason`; gruppnamnet anger typen. Alla listor måste finnas, även om
någon är tom. Python kräver inte att listorna innehåller krav, fyller inte i luckor
och flyttar inte poster mellan grupper. Formatet sparas oförändrat i samma SQLite-tabell
och kan återläsas med `match-show`. Vanligt `match` behåller v2-formatet ovan.

Kommandot sparar giltiga resultat i SQLite och visar dem tillsammans med `result_id`,
`job_id`, titel, annonsversion, innehålls-/profilhash, modell, promptversion och tidpunkt.
`match-show` återläser ett resultat utan modellanrop. Varje körning skapar en ny rad;
annonsversioner och tidigare resultat skrivs inte över. Ogiltiga eller ofullständiga
modellsvar ger exitkod 1 utan resultatpost eller automatiskt återförsök.
För stor indata avvisas före anrop, utan trunkering. Transporten använder som tidigare
bara loopback och ingen molnreserv.

`match` kräver nu YAML och ersätter det äldre A/B/C/REVIEW-kontraktet.
`analyze` och den experimentella granskningen behåller sina äldre TOML-flöden.
Äldre analysmoduler, profil och historiska rapporter finns kvar. De tidigare
match-testerna har bevarats i `var/yaml-match-2026-11-06/before/tests/` och ersatts
i den aktiva testsviten av tester för det nya kontraktet och resultatlagringen.

### Valfri experimentell andra granskning (avstängd i ordinarie flöden)

`jobagent.review` granskar originalannonsen och den råa extraktionen med ett separat
lokalt LLM-anrop. Kandidatprofil, profiljämförelser och rekommendationer skickas inte.
Det vanliga `analyze`-kommandot kör inte granskaren. Programmatisk användning:

```python
from jobagent.review import review_extraction

# job: JobAd, extraction: Extraction, provider: LocalLLM
second_opinion = review_extraction(job, extraction, provider)
```

För sparade JSON-filer finns ett separat kommando:

```powershell
.\.venv\Scripts\python.exe -m jobagent.review --source private/ad.json --extraction private/extraction.json
```

Källfilen ska följa `JobAd`; extraktionsfilen ska innehålla det råa modellkontraktets
`requirements` med passage-ID, typ, nivå, villkorlighet och kontext-ID:n, utan
profilberikade fält. Resultatet innehåller `original_extraction` och `suggestions`
separat. Varje förslag har verifierade originalcitat/positioner, motivering och
`human_review_required`; `applied` är alltid false. Förslag kan vara felaktiga,
överlappa eller missa krav. Det finns ingen automatisk applicering, godkännandestämpel,
matchningspoäng eller bortsortering. För stor indata avvisas utan trunkering;
ofullständiga/ogiltiga svar avvisas utan automatiska återförsök.

## Lagring och avgränsningar

Databasen skapas vid första databaskommandot; `config-check` skapar inga databasfiler.
Fyra tabeller används: `jobs`, `ad_versions`, `search_runs` och `match_results`.
Schema 2 registreras med SQLite `user_version`. Vid uppgradering från schema 1
skapas endast `match_results`; befintliga annons- och sökrader bevaras. Matchresultat
hänvisar till en exakt annonsversion och lagrar resultat-JSON, profilhash, modell,
promptversion och tidpunkt. Annonser identifieras med `(source, external_id)`. Samma annons
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

Analysen består av mekanisk textdelning (`passages.py`), modellstyrd tolkning och
källverifiering (`analysis.py`), separat profiljämförelse (`comparison.py`) samt
konservativ konsolidering (`qualification_groups.py`). Textdelningen bevarar exakta
teckenpositioner och delar även sammantryckta punktlistor. Inga rubriklistor,
automatiska kravtillägg eller regelbaserade ändringar av modellens kravnivå används.

Varje modellval anger `kind` (`qualification`, `condition`, `responsibility`),
`importance`, `conditional` och `context_ids`. `source_passages` och `scope_context`
bevarar originalcitat med `start`/`end` som Python-teckenpositioner. Kontexthänvisningar
verifieras men används inte automatiskt som bevis för kandidatfakta. Det skickade
schemat begränsar alla hänvisningar till annonsens exakta ID:n; okända/förkortade
ID:n avvisas även efter generering. Giltiga men redundanta kontexthänvisningar
bevaras med en varning. Arbetsuppgifter
redovisas separat i `role_responsibilities`. Ofullständig extraktion är möjlig:
`coverage_review` listar ovalda passager för manuell kontroll och påstår inte full täckning.

Det nya modellschemat är strikt och äldre råsvar med bara ID och kravnivå avvisas av
produktionskoden. En uttrycklig adapter används bara i tester för att undersöka vad
som händer med gamla modellval; den är ingen ny semantisk kvalitetsutvärdering.
Tidigare regeltester som krävde automatisk utfyllnad har ersatts av tester för
källpositioner, oförändrad modellklassificering och frånvaro av automatiska tillägg.
De äldre testerna och rapporterna bevaras som lokala baslinjer under `var/`.

Offentliga annonsfixturer och sparade modellval finns i `tests/fixtures/`. Nya frysta
livegranskningar och fullständiga råsvar sparas under `var/`, som inte ingår i Git.
Poäng, automatisk bortsortering och rangordning ingår inte.

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
