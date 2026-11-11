# Lokalt webbflöde och drift

Starta från projektroten:

```powershell
.\.venv\Scripts\python.exe -m jobagent serve
```

Öppna http://127.0.0.1:8765. `--port 8766` väljer annan port. Servern binds till
loopback och använder en enda arbetsprocess. Befintligt CLI fungerar fortfarande.
Hämtning/import anropar ingen modell. LM Studio eller valt OpenAI API behövs först vid analys. Se [OpenAI-inställningar](openai.md).

## Import och analys

Webbformuläret tar källa, titel, originallänk, full annonstext samt valfri arbetsgivare
och ort. Originaltexten bevaras exakt. Ett CLI-alternativ är:

```powershell
.\.venv\Scripts\python.exe -m jobagent import private/ad.json
.\.venv\Scripts\python.exe -m jobagent analyze-pending
.\.venv\Scripts\python.exe -m jobagent analyze 188
```

Importfilen är UTF-8 JSON:

```json
{
  "source": "linkedin",
  "title": "Backendutvecklare",
  "original_url": "https://www.linkedin.com/jobs/view/example",
  "description": "Klistra in hela originalannonsen här",
  "employer": "Exempelföretag",
  "location": "Stockholm"
}
```

Källnamnet normaliseras till gemener. URL-fragment tas bort men query-parametrar
behålls eftersom de kan identifiera jobbet. Samma källa + normaliserad URL uppdaterar
samma manuellt importerade annons. Automatisk import behåller källans externa ID.
Ingen sammanslagning görs över källor eller mellan automatisk och manuell identitet.

`analyze-pending` återanvänder endast senaste lyckade analysen för samma
annonsversion, innehållshash, leverantör, begärd modell och aktuell promptversion. Fel och äldre
analyser tas med vid nästa uttryckliga batchkörning. `analyze ID` analyserar alltid om.

`job-overview-v2` kräver `overview` med `summary`, `techstack`, `education`,
`languages`, `location`, `work_arrangement` utöver tidigare `requirements` och
`experience`. Faktaposter innehåller svensk text och käll-ID:n. Python bygger
originalcitat och positioner; modellen bestämmer tolkningen. Tomma faktalistor
visas som "Ej angivet". Inga numeriska erfarenhetsår gissas av Python.

Strukturellt giltig JSON och giltiga källpositioner garanterar inte semantisk
korrekthet. Kontrollera sammanfattning, kravstyrka och erfarenhet mot originalet.
Techstack visar kandidatkrav respektive omnämnd teknik. Originalcitat visar även
alternativ och kontext. Hela annonsen sparas även när den överskrider modellens
indatagräns; analysförsöket får då felstatus utan trunkering.

Standardbudgeten har höjts till 8 000 utdatatoken för det utökade kontraktet.
Konfigurationsfilen väljer fortfarande modell och lokal server. Ingen automatisk
modellnedladdning eller molnreserv används. OpenAI kan väljas uttryckligen.
Indatagränsen är 40 000 tecken; LM Studios inlästa kontext måste rymma prompt,
schema, annonstext och svar. Ladda modellen med större kontext om servern avvisar indata.

## Webbgränssnitt

- `GET /`: sök i titel, arbetsgivare och originaltext; filtrera källa, ort, teknik,
  analysstatus, erfarenhetsgräns och valfri KEEP/REVIEW/EXCLUDE-bedömning. 40 per sida.
- `GET /jobs/{id}`: aktuell analys och full originaltext med ankarlänkar till källor.
- `GET/POST /import`: manuell import.
- `POST /collect`, `POST /analyze`, `POST /jobs/{id}/analyze`: köa uttryckligt arbete.
- `GET /tasks`, `GET /api/tasks`: framsteg, fel och resultat.
- `POST /tasks/{id}/retry`: starta om avbruten, delvis lyckad eller misslyckad uppgift.

Alla skrivande formulär använder CSRF-token. Host/Origin kontrolleras, externa
HTML-texter escapes och externa skript används inte. Ingen inloggning behövs för
den lokala enanvändarversionen. Exponera inte processen på nätverket.

Listan visar annonser med aktuell eller okänd ansökningstid, sorterade efter
publiceringsdatum med okända publiceringsdatum sist. Hämtning hoppar över nya
annonser med passerad ansökningstid. Datumändringar för redan sparade annonser
uppdateras, och historiken finns kvar. Teknikfilter kräver aktuell analys. Erfarenhetsgränsen
är ett konservativt filter: endast KEEP vid den valda gränsen visas; oanalyserade och
osäkra fall återfinns utan erfarenhetsgräns. Ändrad annons visar aldrig gammal analys
som aktuell. Vid modell-/promptbyte märks tidigare analys som inaktuell.

## Databas och återstart

Schema 4 lägger till `background_tasks`; befintliga tabeller och historiska analyser
bevaras. Före uppgradering av ett befintligt schema tas en SQLite backup API-kopia
bredvid databasen: `*.pre-v4.<UTC-tid>.sqlite3`. Säkerhetskopian innehåller även
committad WAL-data. Stoppa server och andra skrivande processer före återställning;
återställ från backup med SQLite backup API, inte genom att blanda gamla WAL-filer
med en utbytt databasfil.

Köstatus lagras i SQLite. Ett OS-lås per databas hindrar två webbworkers från att
köra samtidigt. Väntande uppgifter fortsätter efter start; tidigare `running` märks
`interrupted` och kräver uttrycklig omstart. Dubbelklick köar inte två identiska
aktiva uppgifter. Vid normal avstängning slutförs pågående begränsade nätanrop och
batchen avbryts vid nästa framstegscheckpoint. Redan sparade annonser/analyser
finns kvar. Vid omstart av batchanalys hoppas färdiga aktuella analyser över.

Kör inte flera CLI-analyser samtidigt med webbköar om du vill undvika dubbla
modellförsök; historiken är append-only och originalannonser påverkas inte.

Automatiska tester använder temporära databaser och mockade nätanrop. Separata
modellprov lagras lokalt under `var/overview-live*`; de ingår inte i Git.
