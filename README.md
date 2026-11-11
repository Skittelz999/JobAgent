# JobAgent — lokal jobbsökning

Lokal jobböversikt med Python 3.12, SQLite, CLI och webbgränssnitt.
Automatisk Platsbanken-hämtning, manuell import från andra källor och lokal LLM-analys.
Ingen AI-server eller modell krävs för sökning och lagring. Docker, webbläsarautomation
och betald API-nyckel behövs inte. Analys använder LM Studio när du uttryckligen startar den.
`upstream-reference` är en orörd referens; implementationen använder inte dess moduler.

## Nytt: tre sammanhängande faser

1. **Samla:** Platsbanken hämtas automatiskt. LinkedIn, Indeed och andra jobbsidor
   importeras med full annonstext och originallänk. [Källstatus](docs/sources.md).
2. **Förstå:** analys via LM Studio eller valbart OpenAI API ger svensk sammanfattning, krav, techstack, erfarenhet,
   utbildning, språk och arbetsvillkor med hänvisningar till originaltexten.
3. **Överblick:** lokal webbvy med sökning, filter och beständig bakgrundskö.

```powershell
.\.venv\Scripts\python.exe -m jobagent serve
# Öppna http://127.0.0.1:8765
```

Installera uppdaterade beroenden med installationskommandona nedan först.
[Kommandon, analyskontrakt, webbflöde och migrering](docs/local-web.md).
[Använd OpenAI API i stället för LM Studio](docs/openai.md).
Databasen uppgraderas till schema 4 med automatisk säkerhetskopia av äldre schema.
Annonser visas efter publiceringsdatum, nyast först. Passerad ansökningstid döljs
och erfarenhetsfiltret är valbart.

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
utan annonstext. `jobagent.exe` finns också i `.venv\Scripts`.
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

Miljövariabler kan överstyra `JOBAGENT_DATABASE` och `JOBAGENT_API_BASE_URL`. Exempel:

```powershell
$env:JOBAGENT_DATABASE = 'E:\AI\JobAgent\var\experiment.sqlite3'
.\.venv\Scripts\python.exe -m jobagent search --query testutvecklare
Remove-Item Env:JOBAGENT_DATABASE
```

CV- och profilbaserad matchning har avlägsnats. `match`, `match-show` och
`--profile` stöds inte längre. Ta bort `[matching]` och eventuella profilsektioner
ur egna inställningsfiler; okända fält avvisas före databasoperationer.
Miljövariabeln `JOBAGENT_MINIMUM_SCORE` har ingen funktion och kan tas bort.
Inga kandidatfiler behöver skapas eller läsas.

## Lokal annonsanalys och erfarenhetsfilter

Starta LM Studios lokala server och ladda modellen i `[llm].model`. Analysera en
eller flera redan lagrade annonser, utan ny hämtning:

```powershell
.\.venv\Scripts\python.exe -m jobagent analyze 188
.\.venv\Scripts\python.exe -m jobagent analyze 6 17 19 27 28
.\.venv\Scripts\python.exe -m jobagent list --decision KEEP
.\.venv\Scripts\python.exe -m jobagent list --decision EXCLUDE
.\.venv\Scripts\python.exe -m jobagent list --decision REVIEW --limit 1000
# Ange analysens analysis_id från analyze/list, inte annonsens ID:
.\.venv\Scripts\python.exe -m jobagent analysis-show 1
```

`analyze` gör ett lokalt modellanrop per annons och sparar varje försök separat,
även fel. En lyckad körning returnerar exitkod 0; modellfel ger exitkod 1 och REVIEW.
Vid flera ID:n fortsätter körningen efter modellfel och returnerar `results`,
`succeeded` och `failed`. Enstaka annonser returneras direkt som tidigare.
Alla angivna ID:n kontrolleras före migrering och modellanrop; ett saknat ID avbryter
hela körningen. Dubbla ID:n i samma kommando analyseras en gång. Ny körning analyserar om.
Avbrott bevarar redan sparade resultat. Ingen annons raderas eller hämtas av analysen.

`[llm]` i `config/settings.toml` styr adress, modell, timeout, tokenbudget och indatagräns.
`JOBAGENT_LLM_BASE_URL` och `JOBAGENT_LLM_MODEL` överstyr adress/modell. Den lokala adressen tillåter endast loopback. OpenAI är en separat, uttryckligt vald
leverantör via `llm.provider`; ingen proxy, omdirigering eller automatisk molnreserv används. Ordinarie insamling
kontaktar aldrig modellen. För stor indata avvisas utan trunkering, ofullständiga svar
avvisas och inga automatiska återförsök görs. Ett sådant försök sparas som fel/REVIEW.

Promptversion `job-overview-v2` behåller den fullständiga ordnade annonsen,
kravklassificering och källreferenser, och begär dessutom ett obligatoriskt
`experience`-objekt. Modellen returnerar fakta och inga filterbeslut. Exempel på detta
objekt (övriga krav finns samtidigt i modellsvarets `requirements`):

```json
{
  "presence": "present",
  "coverage_complete": true,
  "items": [{
    "kind": "professional_experience",
    "minimum_years": 5,
    "maximum_years": null,
    "importance": "mandatory",
    "domain": "Java",
    "alternatives_accepted": false,
    "conditional": false,
    "ambiguous": false,
    "source_ids": ["p0002"],
    "context_ids": ["p0001"]
  }]
}
```

`presence` är `present`, `absent` eller `unclear`. `coverage_complete` är modellens
bedömning av om hela annonsen kunde granskas, ingen garanti för full täckning.
`kind` skiljer yrkeserfarenhet från `education_duration`, `project_duration`,
`other_duration` och `unclear`. `importance` är `mandatory`, `expected`, `preferred`
eller `unclear`. Okända årtal är null; flerårig erfarenhet får inget påhittat tal.
Ett naket numeriskt krav som ”3 års erfarenhet” får min=max=3, ”minst 3 år” min=3
och max=null. Uttryckliga talord som ”fem/five” betyder 5; ”flerårig/several” saknar
numeriskt värde. Öppna gränser som ”över 3 år” markeras otydliga eftersom schemat
saknar en exklusiv gräns, utan avrundning till påhittade fyra år.
Negativa, icke ändliga eller omvända intervall avvisas.

Python bygger exakta citat och teckenpositioner från originalet i
`experience_evidence`; modellen får inte skriva om citaten. Felaktiga eller dubbla
käll-ID:n avvisar hela analysen. Svenska kravsammanfattningar och kravgrupper bevaras.
Motsägande klassificeringar av samma källpassage i den allmänna kravlistan och
erfarenhetsextraktionen avvisas också, med fel/REVIEW som följd.
**Giltiga referenser bevisar inte att modellen har tolkat kravstyrka, år eller
alternativ korrekt.** Den semantiska bedömningen behöver kontrolleras manuellt.

Filtret i `filtering.py` använder endast det validerade modellresultatet och:

```toml
[filter]
max_required_years = 3
```

- KEEP när analysen är komplett och inga obligatoriska minimikrav eller oklarheter
  kan föra beslutet över gränsen. Exakt 3 år och ett intervall som cirka 0–3 år behålls.
- EXCLUDE: minst ett oberoende, entydigt obligatoriskt minimum över gränsen utan
  alternativ, villkorlighet eller oklarhet.
- REVIEW vid beslutspåverkande osäkerhet, exempelvis 2–4/3–5 år, flerårig erfarenhet
  utan angivet minimum, oklara alternativ eller villkor, ofullständig analys eller analysfel.

Meriterande år och tydliga utbildnings-/projekt-/avtalslängder orsakar inte exkludering.
År för olika kompetenser summeras aldrig. Ett tydligt obligatoriskt minimum över
gränsen kan ge EXCLUDE även om ett annat, oberoende krav är oklart. Motstridiga
uppgifter eller oklarheter som hör till minimikravet leder till REVIEW för det kravet.
Ett entydigt obligatoriskt intervall på 4–6 år ger EXCLUDE eftersom dess minimum
överstiger 3. En uttrycklig övre gräns på högst tre år kan räcka för KEEP även när
ett minimum saknas.

Täckningskontrollen jämför de valda kvalifikationerna med erfarenhetsposterna och
söker i hela originaltexten efter tydliga svenska och engelska erfarenhetsuttryck.
En möjlig lucka ger REVIEW med källcitat och koden `missing_experience_coverage`.
Kontrollen använder textmönster som granskningssignaler. EXCLUDE kräver ett
entydigt obligatoriskt minimikrav i den validerade erfarenhetsanalysen.

`list --decision` använder senaste försöket för den aktuella annonsversionen och
innehållshashen. Oanalyserade/ändrade annonser och senaste misslyckade försök ger
REVIEW; ett gammalt EXCLUDE återanvänds inte för en ny version. Listningen filtrerar
före `--limit` och kan kombineras med `--keyword`. Filtergränsen räknas om direkt från
sparade fakta utan LLM. `analysis-show` visar både ursprungligt `saved_filter` och
`current_filter`, källbevis och `is_current_ad_version`. För en historisk version
avser dess filter den historiska annonsen. Sparade analyser från
`experience-extraction-v1` och `experience-extraction-v2` kan räknas om direkt.
Övriga promptversioner behöver ny analys.

Det rena API:t `analyze_job(job, provider)` skriver inte till databasen.
CLI använder `processing.py` för lagring och filter. `analysis-show` och listning
gör inga modellanrop. Den valfria granskaren nedan behåller sitt råa kravkontrakt;
använd `Extraction` med enbart `requirements` för dess indata.

## Manuellt prov med fem riktiga lagrade annonser

Detta prov är separat från den automatiserade testsviten. Följande ID:n finns i den
befintliga lokala databasen; läs hela annonsen före bedömning:

| ID | Annons | Kontrollpunkt |
|---|---|---|
| 6 | Senior Javautvecklare | ”Minst fem års professionell erfarenhet” |
| 17 | Android Developer | ”2–5 years of experience” och dess kompetensområden |
| 19 | Testare till polisbilen | ”Flerårig och aktuell erfarenhet” utan numeriskt minimum |
| 27 | Infrastructure Engineer, Automation | ”A minimum of 3 years” |
| 28 | Junior Androidutvecklare till tekniskt utvecklingsbolag | ”cirka 0–3 års relevant erfarenhet” |

Kontrollpunkterna är originalutdrag, inte förutbestämda facit för hela annonsen.
Andra obligatoriska krav, alternativ och villkor kan påverka slutbeslutet.

1. Starta LM Studio, ladda modellen som anges i `[llm].model` och starta servern på
   `[llm].base_url` (standard `http://127.0.0.1:1234/v1`). Ändra konfigurationen eller
   `JOBAGENT_LLM_MODEL` om modellens server-ID är ett annat.
2. Kör i PowerShell från projektroten. Kontrollera att `config-check` visar rätt
   databas, modell, lokal adress och `max_required_years = 3`:

```powershell
Set-Location E:\AI\JobAgent
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python.exe -m jobagent config-check
foreach ($jobId in @(6, 17, 19, 27, 28)) {
    .\.venv\Scripts\python.exe -m jobagent show $jobId
}
New-Item -ItemType Directory -Force -Path var/experience-live | Out-Null
$batch = .\.venv\Scripts\python.exe -m jobagent analyze 6 17 19 27 28 | ConvertFrom-Json
$analysisExit = $LASTEXITCODE
$batch | ConvertTo-Json -Depth 60 | Set-Content -Encoding utf8 var/experience-live/batch.json
$batch.results | Select-Object ad_id, analysis_id, status, @{Name='decision'; Expression={$_.filter.decision}}
$analysisExit
```

3. Granska varje sparad analys och dess originalcitat. Följande gör inga nya LLM-anrop:

```powershell
foreach ($entry in $batch.results) {
    .\.venv\Scripts\python.exe -m jobagent analysis-show $entry.analysis_id
}
.\.venv\Scripts\python.exe -m jobagent list --decision KEEP --limit 1000
.\.venv\Scripts\python.exe -m jobagent list --decision EXCLUDE --limit 1000
.\.venv\Scripts\python.exe -m jobagent list --decision REVIEW --limit 1000
```

REVIEW-listan innehåller också andra oanalyserade annonser. Kontrollera särskilt
att obligatoriskt/meriterande och yrkesår/utbildningsår skiljs åt, att 3 år inte
exkluderas och att intervall/oklarheter ger REVIEW. Kontrollera domän, alternativ,
kontext och källcitat mot hela annonsen; dokumentera modellfel separat från filterfel.
Exitkod 1 betyder minst ett analysfel; även det försöket ska ha ett sparat ID och REVIEW.
Kör exempelvis `analyze 6` igen för att verifiera omanalys med nytt `analysis_id`
och samma annonsversion/hash, utan hämtning. Inga livekörningar görs av testsviten.

### Valfri experimentell andra granskning (avstängd i ordinarie flöden)

`jobagent.review` granskar originalannonsen och den råa extraktionen med ett separat
LLM-anrop med vald leverantör. Kandidatprofil, profiljämförelser och rekommendationer skickas inte.
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
Aktiva lagringstabeller är `jobs`, `ad_versions`, `search_runs`, `analysis_results` och `background_tasks`.
Den historiska tabellen `match_results` bevaras också.
Schema 4 registreras med SQLite `user_version`. Migrering 3→4 lägger till arbetskön. Migrering 2→3 lägger endast till
`analysis_results` och dess index. Schema 1 uppgraderas via den bevarade migreringen
till schema 2. Befintliga annonser, annonsversioner, rådata, hashvärden och historik
skrivs inte om. Varje analysförsök är en ny rad med exakt annonsversion/hash,
modell, promptversion, tidpunkt, status, resultat eller säkert fel och filterbeslut.
Databaslagret kontrollerar innehållshashen mot den historiska versionen före lagring;
modellanrop görs utanför skrivtransaktioner. Ta en SQLite backup API-kopia före
uppgradering av en egen databas som används med den äldre programversionen.
Historiska matchresultat och deras metadata lämnas orörda. Inget aktivt kommando
läser eller skriver matchresultat. Annonser identifieras med `(source, external_id)`. Samma annons
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
annonsuppslag, korskälla-deduplicering och favoriter och ansökningsspårning
är senare steg. Om processen tvångsavslutas kan en sökkörning stå kvar som `running`;
redan sparade sidor finns kvar. CLI startar ingen bakgrundstjänst; `serve` startar den lokala arbetskön.

## Tester och källor

Analysen består av mekanisk textdelning (`passages.py`), modellstyrd tolkning och
källverifiering (`analysis.py`) samt konservativ konsolidering (`qualification_groups.py`). Textdelningen bevarar exakta
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
Ingen poängsättning, kandidatrangordning eller radering görs; EXCLUDE är ett visningsbeslut.

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

Automatiska tester använder `httpx.MockTransport` och tillfälliga databaser. En
automatisk fixture blockerar riktiga HTTP-transporter. De anropar varken
JobTech eller LM Studio och läser inte referensprojektets databas.

- [JobTech API och Swagger](https://jobsearch.api.jobtechdev.se/)
- [API-specifikation](https://jobsearch.api.jobtechdev.se/swagger.json)

API-specifikationen anger CC0 för annonserna. Personuppgifter i annonser bör ändå
hanteras varsamt. Inga filer, promptar eller mallar har kopierats från GPL-referensen.
Eventuell framtida kodåteranvändning därifrån kräver separat licensbedömning.
