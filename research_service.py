import asyncio
import httpx
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

from claude_service import summarize_research

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"

# Module-level HTTP client — reuses connections across requests.
_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(timeout=20)
    return _http_client


# Core research topics — always kept up to date
BASE_TOPICS = [
    "muscle hypertrophy resistance training",
    "protein synthesis muscle building",
    "body recomposition fat loss muscle retention",
    "creatine supplementation athletic performance",
    "periodization strength training optimal",
    "post workout nutrition protein timing",
    "sleep muscle recovery testosterone",
    "progressive overload hypertrophy",
    "dietary protein requirements resistance training",
    "omega-3 muscle inflammation recovery",
]

# Goal-specific topics appended based on user's objective
GOAL_TOPICS = {
    "bulk": [
        "caloric surplus lean muscle gain",
        "anabolic window post exercise nutrition",
    ],
    "cut": [
        "caloric deficit muscle preservation high protein",
        "intermittent fasting muscle retention",
    ],
    "recomp": [
        "body recomposition simultaneous fat loss muscle gain",
        "high protein recomposition caloric balance",
    ],
    "maintain": [
        "maintenance calories body composition",
        "resistance training maintenance volume",
    ],
}


# ── Evidence grading (office-hours D4: grade-first, recency as tiebreaker) ──────
# PubMed PublicationType → evidence-grade score. Higher = stronger evidence.
# Meta-analyses / systematic reviews outrank single mechanistic/EMG studies, which
# is why grade is the PRIMARY sort key and recency only breaks ties within a grade.
EVIDENCE_GRADE = {
    "meta-analysis": 5,
    "systematic review": 5,
    "randomized controlled trial": 4,
    "controlled clinical trial": 3,
    "clinical trial": 3,
    "comparative study": 2,
    "observational study": 2,
    "review": 2,            # narrative review — weaker than a systematic review
    "journal article": 1,   # default bucket: mechanistic / EMG / cross-sectional
    "case reports": 0,
    "editorial": 0,
    "comment": 0,
    "letter": 0,
}
DEFAULT_GRADE = 1
GRADE_LABELS = {
    5: "meta-analysis/review",
    4: "RCT",
    3: "clinical trial",
    2: "comparative/review",
    1: "study",
    0: "low-grade",
}


def classify_evidence_grade(pub_types: list) -> int:
    """Map a paper's PubMed PublicationType list to an evidence-grade score (higher = stronger)."""
    if not pub_types:
        return DEFAULT_GRADE
    return max(
        (EVIDENCE_GRADE.get(t.strip().lower(), DEFAULT_GRADE) for t in pub_types),
        default=DEFAULT_GRADE,
    )


async def search_pubmed(query: str, max_results: int = 5, years_back: int = 3) -> list:
    client = _get_http_client()
    min_date = (datetime.now() - timedelta(days=365 * years_back)).strftime("%Y/%m/%d")

    search_params = {
        "db": "pubmed",
        "term": f"({query})[Title/Abstract]",
        "retmax": max_results,
        "sort": "relevance",
        "datetype": "pdat",
        "mindate": min_date,
        "retmode": "json",
    }
    # NCBI API key (optional) lifts the rate limit from 3 to 10 req/sec — matters
    # once per-weak-point queries fan out (research-integration T12).
    _api_key = os.getenv("NCBI_API_KEY")
    if _api_key:
        search_params["api_key"] = _api_key

    try:
        resp = await client.get(f"{PUBMED_BASE}/esearch.fcgi", params=search_params)
        data = resp.json()
    except Exception:
        return []

    ids = data.get("esearchresult", {}).get("idlist", [])
    if not ids:
        return []

    await asyncio.sleep(0.35)  # Respect NCBI rate limit (3 req/sec without key)

    fetch_params = {
        "db": "pubmed",
        "id": ",".join(ids),
        "rettype": "abstract",
        "retmode": "xml",
    }
    if _api_key:
        fetch_params["api_key"] = _api_key

    try:
        resp = await client.get(f"{PUBMED_BASE}/efetch.fcgi", params=fetch_params)
        return _parse_pubmed_xml(resp.text)
    except Exception:
        return []


def _parse_pubmed_xml(xml_text: str) -> list:
    papers = []
    try:
        root = ET.fromstring(xml_text)
        for article in root.findall(".//PubmedArticle"):
            try:
                pmid = article.findtext(".//PMID", "")
                title = article.findtext(".//ArticleTitle", "").strip()
                abstract_parts = article.findall(".//AbstractText")
                abstract = " ".join(
                    (t.text or "").strip() for t in abstract_parts
                )
                authors = []
                for author in article.findall(".//Author")[:3]:
                    last = author.findtext("LastName", "")
                    first = author.findtext("ForeName", "")
                    if last:
                        authors.append(f"{last} {first}".strip())
                journal = article.findtext(".//Journal/Title", "")
                year = article.findtext(".//PubDate/Year", "")
                pub_types = [
                    (pt.text or "").strip()
                    for pt in article.findall(".//PublicationType")
                    if (pt.text or "").strip()
                ]
                grade = classify_evidence_grade(pub_types)

                if title and abstract:
                    papers.append({
                        "pmid": pmid,
                        "title": title,
                        "abstract": abstract[:1200],
                        "authors": authors,
                        "journal": journal,
                        "year": year,
                        "publication_types": pub_types,
                        "evidence_grade": grade,
                        "evidence_label": GRADE_LABELS.get(grade, "study"),
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "source": "PubMed",
                    })
            except Exception:
                continue
    except Exception:
        pass
    return papers


async def search_semantic_scholar(query: str, max_results: int = 4) -> list:
    client = _get_http_client()
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,year,authors,citationCount,externalIds",
        "sort": "citationCount",
    }

    try:
        resp = await client.get(
            f"{SEMANTIC_SCHOLAR_BASE}/paper/search", params=params
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except Exception:
        return []

    papers = []
    for paper in data.get("data", []):
        if not paper.get("abstract"):
            continue
        authors = [a.get("name", "") for a in (paper.get("authors") or [])[:3]]
        papers.append({
            "title": paper.get("title", ""),
            "abstract": (paper.get("abstract") or "")[:1200],
            "year": paper.get("year"),
            "authors": authors,
            "citation_count": paper.get("citationCount", 0),
            "url": f"https://www.semanticscholar.org/paper/{paper.get('paperId', '')}",
            "source": "Semantic Scholar",
        })
    return papers


async def get_papers_for_topic(topic: str) -> list:
    pubmed, semantic = await asyncio.gather(
        search_pubmed(topic),
        search_semantic_scholar(topic, max_results=3),
    )

    all_papers = pubmed + semantic
    seen = set()
    unique = []
    for p in all_papers:
        key = p["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


async def _process_topic(topic: str) -> tuple[str, dict] | None:
    try:
        papers = await get_papers_for_topic(topic)
        if not papers:
            return None
        # summarize_research is a synchronous Claude call — run in thread pool
        # so it doesn't block the event loop during batch processing.
        summary = await asyncio.to_thread(summarize_research, topic, papers)
        return topic, {"papers": papers, "summary": summary}
    except Exception as e:
        print(f"Research fetch failed for '{topic}': {e}")
        return None


async def refresh_all_research(profile=None) -> dict:
    topics = BASE_TOPICS.copy()
    if profile and profile.goal and profile.goal in GOAL_TOPICS:
        topics.extend(GOAL_TOPICS[profile.goal])

    results = {}
    # Process in batches of 3 to respect NCBI rate limits while still parallelising.
    batch_size = 3
    for i in range(0, min(len(topics), 10), batch_size):
        batch = topics[i:i + batch_size]
        batch_results = await asyncio.gather(*[_process_topic(t) for t in batch])
        for result in batch_results:
            if result:
                topic, data = result
                results[topic] = data
        if i + batch_size < min(len(topics), 10):
            await asyncio.sleep(1.0)  # pause between batches for NCBI rate limit

    return results


# ── Per-weak-point research (research-integration feature) ──────────────────────
# Maps a normalized physique weak point to PubMed-friendly query terms. A substring
# match lets free-text analysis output ("slightly underdeveloped upper chest") still
# resolve to a targeted query. Longer/more-specific keys are listed before their
# generic parent so the substring loop prefers the specific match.
WEAKPOINT_QUERY_MAP = {
    "upper chest": "incline press upper pectoralis activation hypertrophy",
    "lower chest": "decline press lower pectoralis hypertrophy",
    "chest": "pectoralis chest hypertrophy training",
    "back width": "lat pulldown latissimus width hypertrophy",
    "back thickness": "row back thickness hypertrophy",
    "back": "latissimus back hypertrophy training",
    "side delt": "lateral deltoid lateral raise hypertrophy",
    "rear delt": "posterior deltoid rear delt hypertrophy",
    "shoulder": "deltoid shoulder hypertrophy training",
    "bicep": "biceps brachii hypertrophy training",
    "tricep": "triceps brachii hypertrophy training",
    "arm": "biceps triceps arm hypertrophy",
    "quad": "quadriceps hypertrophy squat training",
    "hamstring": "hamstring hypertrophy training",
    "glute": "gluteus maximus hypertrophy training",
    "calves": "calf gastrocnemius soleus hypertrophy",
    "calf": "calf gastrocnemius soleus hypertrophy",
    "core": "abdominal core hypertrophy training",
}

GOAL_QUERY_HINT = {
    "bulk": "muscle hypertrophy",
    "cut": "muscle preservation caloric deficit",
    "recomp": "body recomposition",
    "strength": "strength training",
    "maintain": "training volume maintenance",
    "prep": "contest preparation muscle retention",
}


def weakpoint_topic_key(weak_point: str, goal: str = "") -> str:
    """Stable ResearchCache topic key for a weak-point + goal pair.

    Reuses the existing ResearchCache.topic column (no schema migration). Research
    for a weak point is identical across users, so the key is intentionally global.
    """
    wp = "_".join((weak_point or "").strip().lower().split())
    g = (goal or "").strip().lower()
    return f"weakpoint:{wp}:{g}" if g else f"weakpoint:{wp}"


def weakpoint_query(weak_point: str, goal: str = "") -> str:
    """Build a PubMed query string for a physique weak point + training goal.

    Falls back to the raw weak-point text so unknown weak points still produce a
    real (if less targeted) query rather than nothing.
    """
    wp = (weak_point or "").strip().lower()
    base = WEAKPOINT_QUERY_MAP.get(wp)
    if base is None:
        for key, terms in WEAKPOINT_QUERY_MAP.items():
            if key in wp:
                base = terms
                break
    if base is None:
        base = f"{wp} hypertrophy training" if wp else "muscle hypertrophy training"
    hint = GOAL_QUERY_HINT.get((goal or "").strip().lower(), "")
    return f"{base} {hint}".strip()


def grade_rank(papers: list) -> list:
    """Sort papers by evidence grade (desc), then publication year (desc).

    Implements the office-hours D4 rule: evidence grade is the master key, recency
    is only the tiebreaker WITHIN a grade. A 2019 meta-analysis outranks a 2024
    single mechanistic study.
    """
    def _key(p):
        grade = p.get("evidence_grade", DEFAULT_GRADE)
        try:
            year = int(p.get("year") or 0)
        except (ValueError, TypeError):
            year = 0
        return (grade, year)
    return sorted(papers, key=_key, reverse=True)


async def fetch_weakpoint_research(weak_point: str, goal: str = "", max_results: int = 6) -> dict:
    """Fetch + grade + rank research for a single physique weak point.

    Returns a dict shaped for ResearchCache persistence (the caller writes the row):
        {"topic": <weakpoint key>, "query": <pubmed query>, "papers": [graded, ranked]}

    Degrades to an empty paper list (never fabricates) when no research is found —
    callers must render a "no strong evidence found" state rather than inventing one.
    """
    query = weakpoint_query(weak_point, goal)
    pubmed, semantic = await asyncio.gather(
        search_pubmed(query, max_results=max_results),
        search_semantic_scholar(query, max_results=3),
    )
    # Semantic Scholar papers carry no PublicationType — default-grade them so the
    # cross-source ranking is well-defined.
    for p in semantic:
        p.setdefault("evidence_grade", DEFAULT_GRADE)
        p.setdefault("evidence_label", GRADE_LABELS[DEFAULT_GRADE])

    seen = set()
    unique = []
    for p in pubmed + semantic:
        key = (p.get("title") or "").lower()[:60]
        if key and key not in seen:
            seen.add(key)
            unique.append(p)

    return {
        "topic": weakpoint_topic_key(weak_point, goal),
        "query": query,
        "papers": grade_rank(unique),
    }


# ── Citation integrity (T5 membership + T7 degrade-on-empty) ────────────────────
_PMID_RE = re.compile(r"PMID:?\s*(\d+)", re.IGNORECASE)


def extract_cited_pmids(text: str) -> list:
    """Pull PMID references (e.g. '[PMID:12345]') from synthesized report prose."""
    return _PMID_RE.findall(text or "")


def verify_report_citations(report: dict, allowed_pmids) -> dict:
    """Citation-integrity gate: deterministic membership (T5) + degrade-on-empty (T7).

    Keeps only citations whose PMID is in ``allowed_pmids`` (the fetched set already
    intersected with the claim-match judge's verdicts). Strips orphan inline
    ``[PMID:x]`` references from the summary. If nothing verifiable remains, marks the
    report degraded with an honest note rather than fabricating a citation.
    """
    allowed = {str(p) for p in allowed_pmids}
    kept = [
        c for c in (report.get("citations") or [])
        if isinstance(c, dict) and str(c.get("pmid")) in allowed
    ]
    summary = report.get("summary", "") or ""
    summary = _PMID_RE.sub(lambda m: m.group(0) if m.group(1) in allowed else "", summary)
    verified = dict(report)
    verified["citations"] = kept
    if not kept:
        verified["degraded"] = True
        verified["summary"] = (
            "No strong, verifiable evidence was found for this weak point yet — "
            "showing no citation rather than an unverified source."
        )
    else:
        verified["degraded"] = False
        verified["summary"] = summary.strip()
    return verified
