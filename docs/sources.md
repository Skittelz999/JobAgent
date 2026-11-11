# Jobbkällor: verifierad integrationsstatus

Kontrollerat 2026-11-11. Automatisk hämtning innebär läsning av annonser till
JobAgents lokala databas, inte publicering av annonser åt arbetsgivare.

| Källa | Stöd i JobAgent | Verifierad åtkomst |
|---|---|---|
| Platsbanken | Automatisk hämtning och manuell import | Befintlig JobTech-adapter, utan modellberoende |
| LinkedIn | Manuell import | Ingen läsbehörighet för detta projekt har verifierats |
| Indeed | Manuell import | Ingen åtkomst för export av fullständig annonstext har verifierats |
| Övriga | Manuell import | Nya adaptrar kan implementera `JobSource.batches(Search)` |

LinkedIns [Apply Connect-dokumentation](https://learn.microsoft.com/en-us/linkedin/talent/apply-connect/create-apply-connect-jobs)
beskriver skapande av jobb genom partnerintegrationer. Det är inte en verifierad
sök-/läsintegration för JobAgent. [LinkedIns utvecklarverktyg](https://github.com/linkedin-developers/job-posting-development-tools)
anger också partnerprogrammet som förutsättning för dessa publiceringsverktyg.

Indeeds [integrationsöversikt](https://docs.indeed.com/job-postings) skiljer mellan
Job Sync/Job Update för arbetsgivar- och ATS-annonser och Publisher JavaScript Plugin
för visning av jobb i en webbplats. En frontend-inbäddning bevisar inte att
JobAgent får hämta och lagra fullständig annonstext för lokal analys.

Ingen fungerande läsintegration eller tillhörande kontobehörighet har verifierats
för LinkedIn/Indeed i den här miljön. Därför aktiveras ingen sådan adapter och inga
inloggningsuppgifter behövs. Den manuella importen hämtar aldrig den angivna URL:en.

En framtida adapter ska validera åtkomst och fullständighet med ett verkligt prov,
implementera källspecifik paginering, normalisering och begränsade återförsök och
leverera `SourceBatch` med normaliserade `SourceAd`, rådata och sökstatus.
`collect_sources` sparar varje batch separat och fortsätter med nästa källa efter
`SourceError`. Programmeringsfel och databasfel maskeras inte som källfel.
