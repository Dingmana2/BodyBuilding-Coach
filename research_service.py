import asyncio
import httpx
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

PUBMED_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
SEMANTIC_SCHOLAR_BASE = "https://api.semanticscholar.org/graph/v1"

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


async def search_pubmed(query: str, max_results: int = 5, years_back: int = 3) -> list:
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

    async with httpx.AsyncClient(timeout=15) as client:
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

    async with httpx.AsyncClient(timeout=20) as client:
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

                if title and abstract:
                    papers.append({
                        "pmid": pmid,
                        "title": title,
                        "abstract": abstract[:1200],
                        "authors": authors,
                        "journal": journal,
                        "year": year,
                        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                        "source": "PubMed",
                    })
            except Exception:
                continue
    except Exception:
        pass
    return papers


async def search_semantic_scholar(query: str, max_results: int = 4) -> list:
    params = {
        "query": query,
        "limit": max_results,
        "fields": "title,abstract,year,authors,citationCount,externalIds",
        "sort": "citationCount",
    }

    async with httpx.AsyncClient(timeout=15) as client:
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


async def refresh_all_research(profile=None) -> dict:
    from claude_service import summarize_research

    topics = BASE_TOPICS.copy()
    if profile and profile.goal and profile.goal in GOAL_TOPICS:
        topics.extend(GOAL_TOPICS[profile.goal])

    results = {}
    for topic in topics[:10]:  # Cap at 10 topics per refresh
        try:
            papers = await get_papers_for_topic(topic)
            if papers:
                summary = summarize_research(topic, papers)
                results[topic] = {"papers": papers, "summary": summary}
            await asyncio.sleep(0.5)  # Gentle rate limiting
        except Exception as e:
            print(f"Research fetch failed for '{topic}': {e}")
            continue

    return results
