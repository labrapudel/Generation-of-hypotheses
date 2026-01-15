import feedparser
from datetime import datetime

ARXIV_API_URL = "http://export.arxiv.org/api/query"

def fetch_arxiv_articles(category="cs.AI", max_results=20):
    query = f"search_query=cat:{category}&start=0&max_results={max_results}"
    url = f"{ARXIV_API_URL}?{query}"

    feed = feedparser.parse(url)
    articles = []

    for entry in feed.entries:
        arxiv_id = entry.id.split('/abs/')[-1]

        articles.append({
            "arxiv_id": arxiv_id,
            "title": entry.title.replace("\n", " ").strip(),
            "abstract": entry.summary.replace("\n", " ").strip(),
            "authors": ", ".join(a.name for a in entry.authors),
            "category": entry.arxiv_primary_category["term"],
            "published": datetime.strptime(entry.published, "%Y-%m-%dT%H:%M:%SZ"),
            "pdf_url": entry.links[1].href
        })

    return articles
