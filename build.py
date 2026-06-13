#!/usr/bin/env python3
"""
SterIndex — build.py
Run this script to refresh the site.

What it does:
  1. Loads archive.json (all previously saved articles, newest first)
  2. Fetches RSS feeds (PubMed, FDA, CDC) — free, no key needed
  3. Merges new articles into the archive (deduplicates by URL)
  4. Calls Ollama (local, free) to write a descriptive summary for each NEW item
  5. Saves each new article as  articles/<slug>.html
  6. Rewrites paginated index pages:
       index.html   → articles  1–30   (latest)
       index2.html  → articles 31–60   etc.
  7. Writes legal.html

Requirements (one-time setup):
  1. Install Ollama:  https://ollama.com/download
  2. Pull a model:    ollama pull mistral
  3. pip install -r requirements.txt

Usage:
  python build.py
"""

import os, re, json, time, textwrap, random
from datetime import datetime, timezone
from pathlib import Path
from math import ceil

import httpx
import xml.etree.ElementTree as ET
from jinja2 import Environment, FileSystemLoader

# ── Config ─────────────────────────────────────────────────────────────────────

OLLAMA_URL   = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "mistral"   # change to "llama3" or "phi3" if preferred

PAGE_SIZE     = 30
OUTPUT_DIR    = Path(__file__).parent
ARTICLES_DIR  = OUTPUT_DIR / "articles"
TEMPLATES_DIR = OUTPUT_DIR / "templates"
ARCHIVE_FILE  = OUTPUT_DIR / "archive.json"

ARTICLES_DIR.mkdir(exist_ok=True)

# ── RSS Sources ────────────────────────────────────────────────────────────────

RSS_FEEDS = [
    # ── PubMed searches (always reliable) ──────────────────────────────────────
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=surgical+instrument+sterilization&format=abstract&limit=10"),
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=endoscope+reprocessing+sterilization&format=abstract&limit=10"),
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=autoclave+steam+sterilization+hospital&format=abstract&limit=10"),
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=sterile+processing+operating+room&format=abstract&limit=10"),
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=medical+device+decontamination+standards&format=abstract&limit=8"),
    ("PubMed", "https://pubmed.ncbi.nlm.nih.gov/rss/search/?term=hospital+infection+prevention+sterilization&format=abstract&limit=8"),

    # ── FDA ────────────────────────────────────────────────────────────────────
    ("FDA MedWatch", "https://www.fda.gov/about-fda/contact-fda/stay-informed/rss-feeds/medwatch/rss.xml"),

    # ── WHO ────────────────────────────────────────────────────────────────────
    ("WHO", "https://www.who.int/rss-feeds/news-english.xml"),

    # ── CDC Infection Control ──────────────────────────────────────────────────
    ("CDC", "https://tools.cdc.gov/api/v2/resources/media/316422.rss"),

    # ── Infection Control Today ────────────────────────────────────────────────
    ("Infection Control Today", "https://www.infectioncontroltoday.com/rss.xml"),

    # ── Journal of Hospital Infection ──────────────────────────────────────────
    # (may require institutional access — skipped silently if unavailable)
    ("Journal of Hospital Infection", "https://www.journalofhospitalinfection.com/rss/articles"),

    # ── ECRI Institute ─────────────────────────────────────────────────────────
    # (may require login — skipped silently if unavailable)
    ("ECRI Institute", "https://www.ecri.org/components/HRC/Pages/rss.aspx"),

    # ── AAMI News ──────────────────────────────────────────────────────────────
    # (may require login — skipped silently if unavailable)
    ("AAMI", "https://www.aami.org/news/rss"),
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    text = text.lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text).strip("-")
    return text[:80]


def unique_slug(title: str, existing: set) -> str:
    base = slugify(title)
    slug, n = base, 2
    while slug in existing:
        slug = f"{base}-{n}"
        n += 1
    existing.add(slug)
    return slug


def clean_html(raw: str) -> str:
    if not raw:
        return ""
    text = re.sub(r"<[^>]+>", " ", raw)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]


def fmt_date(raw: str) -> str:
    for fmt in (
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S %Z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S%z",
    ):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%B %d, %Y")
        except Exception:
            pass
    return raw[:20] if raw else ""


def page_filename(n: int) -> str:
    return "index.html" if n == 1 else f"index{n}.html"


# ── Archive ────────────────────────────────────────────────────────────────────

def load_archive() -> list[dict]:
    if ARCHIVE_FILE.exists():
        try:
            return json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  [WARN] Could not read archive: {e}")
    return []


def save_archive(articles: list[dict]) -> None:
    ARCHIVE_FILE.write_text(
        json.dumps(articles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ── RSS Parsing ────────────────────────────────────────────────────────────────

def parse_feed(xml_text: str, source_name: str) -> list[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        print(f"  [XML error] {e}")
        return items

    ns = {"atom": "http://www.w3.org/2005/Atom"}

    for item in root.findall(".//item"):
        title = clean_html(getattr(item.find("title"),       "text", "") or "")
        link  = (getattr(item.find("link"),        "text", "") or "").strip()
        desc  = clean_html(getattr(item.find("description"), "text", "") or "")
        date  = fmt_date(getattr(item.find("pubDate"),       "text", "") or "")
        if title and link:
            items.append({"title": title, "link": link,
                          "description": desc, "published": date,
                          "source": source_name})

    if not items:
        for entry in root.findall("atom:entry", ns):
            title_el   = entry.find("atom:title",   ns)
            link_el    = entry.find("atom:link",     ns)
            summary_el = entry.find("atom:summary",  ns)
            updated_el = entry.find("atom:updated",  ns)

            title = clean_html(getattr(title_el,   "text", "") or "")
            link  = ((link_el.get("href", "") if link_el is not None else "") or "").strip()
            desc  = clean_html(getattr(summary_el, "text", "") or "")
            date  = fmt_date(getattr(updated_el,   "text", "") or "")

            if title and link:
                items.append({"title": title, "link": link,
                              "description": desc, "published": date,
                              "source": source_name})
    return items


def fetch_fresh_articles() -> list[dict]:
    all_items: list[dict] = []
    seen: set[str] = set()
    headers = {"User-Agent": "SterIndex-Bot/1.0 (sterile-tech news aggregator)"}

    with httpx.Client(timeout=15.0, follow_redirects=True, headers=headers) as client:
        for source_name, url in RSS_FEEDS:
            print(f"  Fetching {source_name}: {url[:70]}…")
            try:
                resp = client.get(url)
                resp.raise_for_status()
                items = parse_feed(resp.text, source_name)
                added = 0
                for item in items:
                    if item["link"] not in seen:
                        seen.add(item["link"])
                        all_items.append(item)
                        added += 1
                print(f"    → {added} new items in feed")
            except Exception as e:
                print(f"    [WARN] Failed: {e}")
    return all_items


# ── Ollama Summarizer ──────────────────────────────────────────────────────────

def ai_article(raw: dict) -> str:
    """
    Write a descriptive-only summary — no conclusions, no advice, no evaluation.
    Uses Ollama running locally (free, no API key needed).
    """

    # Four opening styles — varied so pages don't share the same sentence rhythm
    openings = [
        "Open with the subject and scope of the source. Describe what was studied or reported and how it was carried out.",
        "Open with the setting or context — the type of device, procedure, or facility involved. Then describe what was examined and what was found.",
        "Open with the method or approach used in the source. Then describe the subject matter and the results as reported.",
        "Open with the specific topic the source addresses. Describe the scope of the work and the results or content as presented.",
    ]

    prompt = textwrap.dedent(f"""
        You are a medical news writer. Your only job is to describe what a source says.
        You do not interpret, evaluate, advise, or draw conclusions of any kind.
        You do not tell readers what to do, what matters, or what to think.
        You only describe the content of the source in plain, factual prose.

        Write a descriptive summary of 120 to 160 words based on the source below.
        Plain prose only. No bullet points, no headings, no markdown.

        Opening style to use: {random.choice(openings)}

        Rules — follow every one strictly:
        - Describe only. Never write phrases like: "practitioners should",
          "this highlights the need for", "it is important to", "the findings suggest
          that clinicians", "departments are encouraged", or any other advisory,
          evaluative, or concluding language.
        - No conclusions. Do not end with a takeaway, recommendation, or significance
          statement. Stop after describing the content.
        - Vary sentence length naturally. Short sentences and longer ones mixed.
        - Use contractions where they sound natural: don't, it's, they've.
        - Active voice almost always.
        - Never open with: "This study", "Researchers have found", "In this article".
        - Never use: furthermore, it is worth noting, in conclusion, delve,
          leverage as a verb, comprehensive, significant as filler, crucial as filler.
        - Use specific numbers and details from the source when available.
        - Do not repeat the title word for word.

        Source title : {raw['title']}
        Source       : {raw['source']}
        Published    : {raw['published']}
        Raw abstract : {raw['description']}

        Return ONLY the descriptive text. No title, no label, no intro line, no sign-off.
    """).strip()

    try:
        resp = httpx.post(
            OLLAMA_URL,
            json={
                "model":  OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature":    0.85,
                    "top_p":          0.92,
                    "repeat_penalty": 1.1,
                    "num_predict":    400,
                },
            },
            timeout=120.0,
        )
        resp.raise_for_status()
        text = resp.json().get("response", "").strip()
        if not text:
            raise ValueError("Empty response from Ollama")
        return text
    except httpx.ConnectError:
        print("    [Ollama error] Cannot connect — is Ollama running? Try: ollama serve")
        return raw.get("description") or "Summary unavailable."
    except Exception as e:
        print(f"    [Ollama error] {e}")
        return raw.get("description") or "Summary unavailable."


# ── HTML Generation ────────────────────────────────────────────────────────────

def load_env() -> Environment:
    return Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)


def write_article_page(env: Environment, article: dict) -> None:
    tmpl = env.get_template("article.html")
    html = tmpl.render(**article)
    (ARTICLES_DIR / f"{article['slug']}.html").write_text(html, encoding="utf-8")


def write_index_pages(env: Environment, all_articles: list[dict], built_at: str) -> int:
    total       = len(all_articles)
    total_pages = ceil(total / PAGE_SIZE) if total else 1
    tmpl        = env.get_template("index.html")

    for page_num in range(1, total_pages + 1):
        start     = (page_num - 1) * PAGE_SIZE
        page_arts = all_articles[start : start + PAGE_SIZE]

        pagination = {
            "current":     page_num,
            "total_pages": total_pages,
            "has_prev":    page_num > 1,
            "has_next":    page_num < total_pages,
            "prev_file":   page_filename(page_num - 1),
            "next_file":   page_filename(page_num + 1),
            "pages": [
                {"num": p, "file": page_filename(p), "active": p == page_num}
                for p in range(1, total_pages + 1)
            ],
        }

        html = tmpl.render(
            articles   = page_arts,
            built_at   = built_at,
            total_all  = total,
            page_size  = PAGE_SIZE,
            start_num  = start + 1,
            pagination = pagination,
        )

        out_path = OUTPUT_DIR / page_filename(page_num)
        out_path.write_text(html, encoding="utf-8")
        print(f"  Wrote {out_path.name}  ({len(page_arts)} articles, page {page_num}/{total_pages})")

    return total_pages


def write_legal_page(env: Environment, built_at: str) -> None:
    tmpl = env.get_template("legal.html")
    html = tmpl.render(built_at=built_at)
    (OUTPUT_DIR / "legal.html").write_text(html, encoding="utf-8")
    print("  Wrote legal.html")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n═══════════════════════════════════")
    print("  SterIndex — build.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═══════════════════════════════════\n")

    built_at = datetime.now(timezone.utc).strftime("%B %d, %Y at %H:%M UTC")

    # 1. Load existing archive
    print("► Loading archive…")
    archive      = load_archive()
    archive_urls = {a["source_url"] for a in archive}
    print(f"  {len(archive)} articles already in archive\n")

    # 2. Fetch fresh RSS
    print("► Fetching RSS feeds…")
    fresh_raw = fetch_fresh_articles()
    print(f"\n  {len(fresh_raw)} total items fetched from feeds")

    # 3. Find genuinely new articles
    new_raw = [r for r in fresh_raw if r["link"] not in archive_urls]
    print(f"  {len(new_raw)} new articles to process\n")

    if not new_raw and not archive:
        print("✗ No articles found. Check your network or RSS URLs.")
        return

    # 4. Summarize new articles + write article pages
    env        = load_env()
    used_slugs = {a["slug"] for a in archive}
    new_articles: list[dict] = []

    if new_raw:
        print("► Generating descriptive summaries…")
        for i, raw in enumerate(new_raw, 1):
            print(f"  [{i:02d}/{len(new_raw)}] {raw['title'][:68]}…")
            slug    = unique_slug(raw["title"], used_slugs)
            content = ai_article(raw)
            article = {
                "slug":       slug,
                "title":      raw["title"],
                "source":     raw["source"],
                "source_url": raw["link"],
                "published":  raw["published"],
                "content":    content,
                "built_at":   datetime.now(timezone.utc).strftime("%B %d, %Y"),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            }
            write_article_page(env, article)
            new_articles.append(article)
            if i < len(new_raw):
                time.sleep(0.2)
    else:
        print("  No new articles — skipping Ollama summarization\n")

    # 5. Merge into archive (newest first)
    all_articles = new_articles + archive
    save_archive(all_articles)
    print(f"\n► Archive now contains {len(all_articles)} total articles")

    # 6. Write paginated index pages
    print("\n► Writing paginated index pages…")
    total_pages = write_index_pages(env, all_articles, built_at)

    # 7. Write legal page
    print("\n► Writing legal.html…")
    write_legal_page(env, built_at)

    print(f"\n✓ Done!")
    print(f"  {len(new_articles)} new articles added")
    print(f"  {len(all_articles)} total in archive")
    print(f"  {total_pages} index page(s) written\n")


if __name__ == "__main__":
    main()
