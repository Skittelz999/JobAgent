# OpenAI API som alternativ till LM Studio

Lokal modell är standard. För att välja OpenAI i samma PowerShell-fönster som
startar JobAgent:

```powershell
$env:JOBAGENT_LLM_PROVIDER = 'openai'
$env:JOBAGENT_OPENAI_MODEL = 'gpt-4.1-mini'
# Ange OPENAI_API_KEY i miljön utan att spara nyckeln i projektet.
$secret = Read-Host 'OpenAI API-nyckel' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
Remove-Variable secret
.\.venv\Scripts\python.exe -m jobagent config-check
.\.venv\Scripts\python.exe -m jobagent serve
```

Öppna http://127.0.0.1:8765. Stoppa en redan startad server innan du startar den med
ny leverantör. Webbvyn visar **OpenAI API**, modellnamnet och att annonstext skickas
till OpenAI vid analys. API-användning debiteras på det anslutna OpenAI-kontot.
Hämtning och import kontaktar fortfarande ingen modell.

CLI använder samma inställningar:

```powershell
.\.venv\Scripts\python.exe -m jobagent analyze 188
.\.venv\Scripts\python.exe -m jobagent analyze-pending
```

För beständigt leverantörsval kan `[llm]` i `config/settings.toml` innehålla
`provider = "openai"` och `openai_model = "gpt-4.1-mini"`, eller så kan du skapa en lokal
`.env`-fil i projektroten (utifrån `.env.example`) med `OPENAI_API_KEY`, `JOBAGENT_LLM_PROVIDER`
och `JOBAGENT_OPENAI_MODEL`. Befintliga miljövariabler i terminalen har alltid företräde framför `.env`.
`model` och `base_url` behålls för LM Studio. Nyckeln läses via miljövariabeln `OPENAI_API_KEY`;
den ingår inte i inställningsmodellen, config-check, analysresultat eller databasen.
Ingen nyckel skickas till LM Studio. Nyckeln skickas endast till den fasta adressen
`https://api.openai.com/v1/responses`; HTTP-omdirigeringar följs inte.

Återgå till lokal modell med `$env:JOBAGENT_LLM_PROVIDER = 'local'` och starta om
servern. Modeller väljs via konfiguration; det sker aldrig automatisk övergång till
en annan leverantör. Modell-/leverantörsbyte kräver ny analys, medan ett returnerat
OpenAI-snapshotnamn inte orsakar upprepade analyser av oförändrade annonser.

Integrationen använder Responses API, `text.format` med strikt JSON Schema och
`store: false`. Det sistnämnda är inte ett löfte om noll datalagring hos leverantören.
Den skickar annonstext och analysinstruktioner, inte CV eller kandidatprofil.
Ofullständiga svar, refusals, nätfel och 401/403/429 sparas som misslyckade försök;
råa API-fel och nycklar skrivs inte ut. Inga automatiska betalda återförsök görs.

Verifierat med mockade API-anrop: autentisering, schema, fel/refusal, tokenavbrott,
leverantörsbyte, modellalias och hela lagrings-/webbflödet. Verkligt OpenAI-anrop
kräver din API-nyckel och har inte körts i denna implementation.

Officiella källor, kontrollerade 2026-11-11:
- [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create)
- [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [GPT-4.1 mini](https://developers.openai.com/api/docs/models/gpt-4.1-mini)
