"""Conservative comparisons against explicit profile facts, without inference calls."""
import re

from jobagent.settings import Profile

POLICY_VERSION = "profile-evidence-v6"

# Each implication is narrow: it supports related knowledge, not years, proficiency
# or professional experience. No implication is made from a fullstack role to a stack.
IMPLICATIONS = {
    "postgresql": (r"\bsql\b", "SQL", "PostgreSQL ger stöd för relaterad SQL-kunskap."),
    "junit": (r"automattest|automat\w* test|testautomat|test, verifiering", "testautomation", "JUnit är relevant testkompetens."),
    "mockmvc": (r"automattest|automat\w* test|testautomat|test, verifiering", "testautomation", "MockMvc är relevant testkompetens."),
    "testcontainers": (r"automattest|automat\w* test|testautomat|test, verifiering", "testautomation", "Testcontainers är relevant testkompetens."),
    "spring boot": (r"\bspring mvc\b", "Spring MVC", "Spring Boot är närliggande kompetens, inte bevis på Spring MVC-erfarenhet."),
    "docker": (r"container(?:baserad\w*|teknik|miljö\w*|-virtualisering)", "containerkunskap", "Docker ger relaterat stöd för containerkunskap, inte för Kubernetes eller microservices."),
}
ALIASES = {"java": r"\bjavautveckl\w*\b", "vue.js": r"\bvue(?:\.js)?\b"}


def skill_evidence(quote: str, profile: Profile) -> list[dict]:
    evidence = []
    for index, skill in enumerate(profile.skills):
        # Word boundaries keep Java separate from JavaScript and SQL from NoSQL.
        pattern = r"(?<!\w)" + re.escape(skill) + r"(?!\w)"
        if re.search(pattern, quote, re.IGNORECASE) or (skill.casefold() in ALIASES and re.search(ALIASES[skill.casefold()], quote, re.IGNORECASE)):
            evidence.append({"profile_path": f"skills[{index}]", "value": skill,
                             "kind": "listed_skill", "supports": skill,
                             "note_sv": "Listad kunskap; nivå och antal yrkesår är inte dokumenterade."})
        implication = IMPLICATIONS.get(skill.casefold())
        if implication and re.search(implication[0], quote, re.IGNORECASE):
            evidence.append({"profile_path": f"skills[{index}]", "value": skill,
                             "kind": "related_knowledge", "supports": implication[1], "note_sv": implication[2]})
    role_topic = bool(re.search(r"fullstack|frontend|backend", quote, re.IGNORECASE))
    general_development = bool(re.search(r"systemutveckling|mjukvaruutveckling|software development", quote, re.IGNORECASE)
                               and re.search(r"erfarenhet|experience|arbete|work", quote, re.IGNORECASE))
    specialized = bool(re.search(r"säkerhet|security|arkitektur|architecture|\bbff\b|\bai\b|\bllms?\b|typescript|node\.js|nestjs|\w*utbildning|\w*examen|degree", quote, re.IGNORECASE))
    if (role_topic or general_development) and not specialized:
        for index, role in enumerate(profile.roles):
            if "fullstack" in role.casefold():
                evidence.append({"profile_path": f"roles[{index}]", "value": role,
                                 "kind": "documented_role", "supports": "fullstack/frontend/backend",
                                 "note_sv": "Fullstackarbete anges uttryckligen; specifika ramverk följer inte automatiskt."})
    if re.search(r"programmeringskunskap|programming (?:skills|knowledge)|kunskap\w* (?:i|inom) programmering", quote, re.IGNORECASE):
        for index, skill in enumerate(profile.skills):
            if skill.casefold() in {"java", "python", "javascript", "c#", "c++", "kotlin"}:
                evidence.append({"profile_path": f"skills[{index}]", "value": skill,
                                 "kind": "related_knowledge", "supports": "programmeringskunskap",
                                 "note_sv": "Listat programmeringsspråk; nivå, yrkesår och personligt intresse följer inte automatiskt."})
    if re.search(r"\b(?:rest|apis?)\b", quote, re.IGNORECASE) and not re.search(r"\bapi gateway\b", quote, re.IGNORECASE):
        for index, detail in enumerate(profile.experience_details):
            if re.search(r"\brest api\b", detail, re.IGNORECASE):
                evidence.append({"profile_path": f"experience_details[{index}]", "value": detail,
                                 "kind": "documented_activity", "supports": "REST API",
                                 "note_sv": "REST API-utveckling är uttryckligen dokumenterad."})
    return evidence


def degree_condition(quote: str) -> str:
    """Return degree / experience_alternative / ambiguous / none.

    A different field of study is not an experience alternative. Bare 'equivalent'
    remains ambiguous rather than being expanded into a qualification we invented.
    """
    if not re.search(r"\b\w*examen\b|\bdegrees?\b", quote, re.IGNORECASE):
        return "none"
    if re.search(r"(?:motsvarande|likvärdig\w*|equivalent)[^.;\n]{0,60}(?:erfarenhet|kunskap|kompetens|experience|knowledge|competence)", quote, re.IGNORECASE):
        return "experience_alternative"
    if re.search(r"\b(?:eller|alternativt|or)\b[^.;\n]{0,20}(?:erfarenhet\w*|kunskap\w*|kompetens\w*|experience|knowledge|qualifications?)[^;\n]{0,100}(?:likvärdig\w*|motsvarande|equivalent)", quote, re.IGNORECASE):
        return "experience_alternative"
    if re.search(r"(?:motsvarande|likvärdig\w*|equivalent)\s+(?:utbildning|education|qualification)", quote, re.IGNORECASE):
        return "ambiguous"
    if re.search(r"(?:motsvarande|equivalent)\s*[).,]?$", quote, re.IGNORECASE):
        return "ambiguous"
    if re.search(r"\b(?:eller|or)\s+(?:motsvarande|equivalent)\b", quote, re.IGNORECASE) and not re.search(r"(?:motsvarande|equivalent)\s+(?:relevant\s+)?(?:område|inriktning|ämne|field|discipline)", quote, re.IGNORECASE):
        return "ambiguous"
    return "degree"


def compare_requirement(requirement: dict, profile: Profile) -> None:
    """Set factual evidence/status and replace unverified candidate assertions.

    'not_met' is an explicit discrepancy with the supplied profile, never an
    automatic application decision. Work duration is not converted into FTE years.
    """
    quote = requirement["quote"]
    evidence = skill_evidence(quote, profile)
    condition = degree_condition(quote)
    keys = {entry["profile_path"].split("[")[0] for entry in evidence}
    match = "unknown"
    explanation = "Profilen innehåller inte tillräckliga uppgifter för att bedöma detta villkor."
    if evidence:
        match = "partial"
        listed = ", ".join(dict.fromkeys(entry["value"] for entry in evidence))
        explanation = f"Dokumenterat stöd i profilen: {listed}. Det bevisar inte samtliga delvillkor, kunskapsnivåer eller yrkesår."
        if re.fullmatch(r"\s*(?:erfarenhet av )?fullstack(?:utveckling)?[.\s]*", quote, re.IGNORECASE) and any(e["kind"] == "documented_role" for e in evidence):
            match = "supported"
            explanation = "Fullstackarbete finns uttryckligen dokumenterat i profilen."
        if (re.search(r"\brest\b", quote, re.IGNORECASE) and any(e["kind"] == "documented_activity" for e in evidence)
                and not re.search(r"\d|\boch\b|\band\b|mycket|advanced|expert", quote, re.IGNORECASE)):
            match = "supported"
            explanation = "REST API-utveckling finns uttryckligen dokumenterad i profilen."
    if condition != "none":
        keys.update({"has_issued_degree", "university_credits"})
        evidence.append({"profile_path": "has_issued_degree", "value": profile.has_issued_degree,
                         "kind": "explicit_fact", "supports": "degree_status",
                         "note_sv": "Avser utfärdad examen, inte ett antagande om studierna är slutförda."})
        evidence.append({"profile_path": "university_credits", "value": profile.university_credits,
                         "kind": "documented_education", "supports": "studies_not_issued_degree",
                         "note_sv": "Studiepoäng är inte ett examensbevis eller ett fastställt likvärdighetsbeslut."})
        if not profile.has_issued_degree and condition == "degree":
            match = "not_met"
            explanation = "Känd avvikelse enligt profilen: ingen utfärdad examen. Annonsens examensvillkor anger inget uttryckligt erfarenhetsalternativ. Detta är inte ett beslut om ansökan."
        elif condition == "experience_alternative":
            match = "partial"
            explanation = "Villkoret tillåter motsvarande erfarenhet/kunskaper. Profilens studier och deltidsarbete är relevanta, men likvärdigheten måste bedömas; den är inte fastställd."
        else:
            match = "unknown"
            explanation = "Profilen fastställer inte examensnivå, inriktning eller likvärdighet med hela villkoret. Kontrollera detta manuellt."
    years = re.search(r"\b(?:minst|minimum(?: of)?|at least)\s+(\d+|ett|en|två|tre|fyra|fem|sex|sju|åtta|nio|tio)\s*(?:års?|years?)", quote, re.IGNORECASE)
    number_words = {"ett": 1, "en": 1, "två": 2, "tre": 3, "fyra": 4, "fem": 5, "sex": 6, "sju": 7, "åtta": 8, "nio": 9, "tio": 10}
    experience = re.search(r"erfarenhet|experience", quote, re.IGNORECASE)
    education = re.search(r"\b\w*utbildning\b|\b\w*examen\b|\bdegrees?\b|\bstud(?:y|ies)\b", quote, re.IGNORECASE)
    current_admission = bool(re.search(r"antagen|pågående studier|current(?:ly)? (?:enroll|admi)|enrolled|currently studying", quote, re.I))
    if education and condition == "none" and not current_admission and re.search(r"högskol|universitet|eftergymnasial|akademisk|college|university", quote, re.I) and profile.university_credits > 0:
        keys.add("university_credits")
        evidence.append({"profile_path": "university_credits", "value": profile.university_credits,
                         "kind": "documented_education", "supports": "relevant_studies",
                         "note_sv": "Studier ger relevant stöd, men utbildningens likvärdighet är inte fastställd."})
        match = "partial"
        explanation = "Profilen dokumenterar högskolestudier. Villkoret anger inte uttryckligen utfärdad examen; relevans och eventuell likvärdighet behöver bedömas."
    if years and experience and not education:
        keys.update({"experience_years", "experience_basis"})
        evidence.append({"profile_path": "experience_years", "value": profile.experience_years,
                         "kind": "explicit_duration", "supports": "total_duration_not_area_specific_years",
                         "note_sv": f"Angiven omfattning: {profile.experience_basis}; ingen heltidsomräkning."})
        minimum = int(years.group(1)) if years.group(1).isdigit() else number_words[years.group(1).casefold()]
        if profile.experience_years < minimum:
            match = "not_met"
            explanation = f"Känd avvikelse enligt angiven profil: cirka {profile.experience_years:g} års arbete mot minst {years.group(1)} år. Ingen heltidsomräkning görs; erfarenhet per teknik och efterfrågad tidsperiod behöver fortfarande verifieras."
        else:
            match = "partial" if any(e["kind"] != "explicit_duration" for e in evidence) else "unknown"
            explanation = "Profilens totala arbetsperiod bevisar inte efterfrågade yrkesår inom just detta område eller angiven tidsperiod. Ingen heltidsomräkning görs."
    elif experience and not education and re.search(r"flerår|några år|several years|few years", quote, re.IGNORECASE):
        keys.update({"experience_years", "experience_basis"})
        match = "partial" if evidence else "unknown"
        explanation = "Profilen anger deltidsarbete men bevisar inte yrkesår inom det efterfrågade området. Formuleringen anger inget numeriskt minimum; det är inte fastställt att erfarenhetsvillkoret är uppfyllt."
        evidence.append({"profile_path": "experience_years", "value": profile.experience_years,
                         "kind": "explicit_duration", "supports": "total_duration_not_area_specific_years",
                         "note_sv": "Omfattningen är angiven som deltid; inga heltidsår eller områdesspecifika år antas."})
    if current_admission:
        evidence = []
        keys = set()
        match = "unknown"
        explanation = "Historiska studiepoäng bevisar inte aktuell antagning eller pågående studier. Aktuell status saknas i profilen."
    requirement["profile_evidence"] = evidence
    requirement["profile_fact_keys"] = sorted(keys)
    requirement["match"] = match
    requirement["explanation_sv"] = explanation
