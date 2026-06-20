#!/usr/bin/env python3
"""
SterIndex — generate_blog.py
Publishing subsystem: builds the blog index and individual post pages
from Markdown source files in /content/articles/ and /content/digests/.

Requirements:
    pip install python-frontmatter markdown

Usage:
    python generate_blog.py
"""

import os
import glob
from datetime import datetime, date
from pathlib import Path

import frontmatter
import markdown as md_lib

# ── Config ─────────────────────────────────────────────────────────────────────

ROOT_DIR        = Path(__file__).parent
CONTENT_DIR     = ROOT_DIR / "content"
ARTICLES_DIR    = CONTENT_DIR / "articles"
DIGESTS_DIR     = CONTENT_DIR / "digests"
TEMPLATES_DIR   = ROOT_DIR / "templates"
OUTPUT_DIR      = ROOT_DIR / "blog"          # individual post pages go here
INDEX_OUTPUT    = ROOT_DIR / "blog.html"     # blog index page (root level)

POST_TEMPLATE_PATH  = TEMPLATES_DIR / "post_template.html"
INDEX_TEMPLATE_PATH = TEMPLATES_DIR / "blog_index_template.html"

AUTHOR_NAME = "Alexander Yakubovski, Senior Compliance Analyst"

MD_EXTENSIONS = ["extra", "smarty", "sane_lists"]

ARTICLES_DIR.mkdir(parents=True, exist_ok=True)
DIGESTS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text: str) -> str:
    import re
    s = text.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:80]


def parse_date(value) -> date:
    """Front matter dates may arrive as date, datetime, or string."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%B %d, %Y"):
            try:
                return datetime.strptime(value.strip(), fmt).date()
            except ValueError:
                continue
    return date.today()


def load_posts_from(folder: Path, post_type: str) -> list[dict]:
    """Load and parse every .md file in a folder into a post dict."""
    posts = []
    for path in sorted(glob.glob(str(folder / "*.md"))):
        try:
            post = frontmatter.load(path)
        except Exception as e:
            print(f"  [WARN] Could not parse {path}: {e}")
            continue

        title    = post.get("title", Path(path).stem.replace("-", " ").title())
        raw_date = post.get("date", date.today())
        parsed_date = parse_date(raw_date)
        post_type_meta = post.get("type", post_type)

        slug = post.get("slug") or slugify(title)
        html_content = md_lib.markdown(post.content, extensions=MD_EXTENSIONS)

        posts.append({
            "title":        title,
            "date":         parsed_date,
            "date_display": parsed_date.strftime("%B %d, %Y"),
            "type":         post_type_meta,
            "slug":         slug,
            "content_html": html_content,
            "source_path":  path,
        })
    return posts


def load_all_posts() -> list[dict]:
    articles = load_posts_from(ARTICLES_DIR, "Article")
    digests  = load_posts_from(DIGESTS_DIR, "Digest")
    all_posts = articles + digests
    # CRITICAL: sort descending by date — newest first
    all_posts.sort(key=lambda p: p["date"], reverse=True)
    return all_posts


# ── Rendering ──────────────────────────────────────────────────────────────────

def render_post_page(post: dict, template: str) -> str:
    html = template
    html = html.replace("{{ title }}",   post["title"])
    html = html.replace("{{ date }}",    post["date_display"])
    html = html.replace("{{ type }}",    post["type"])
    html = html.replace("{{ author }}",  AUTHOR_NAME)
    html = html.replace("{{ content }}", post["content_html"])
    return html


def render_index_row(post: dict) -> str:
    type_class = "tag-article" if post["type"].lower() == "article" else "tag-digest"
    return f'''
    <a class="blog-row" href="blog/{post['slug']}.html">
      <span class="blog-row-date">{post['date_display']}</span>
      <span class="blog-row-type {type_class}">{post['type']}</span>
      <span class="blog-row-title">{post['title']}</span>
      <span class="blog-row-arrow">&rarr;</span>
    </a>'''


def render_index_page(posts: list[dict], template: str) -> str:
    rows_html = "\n".join(render_index_row(p) for p in posts)
    if not posts:
        rows_html = '<div class="blog-empty">No posts published yet.</div>'

    html = template
    html = html.replace("{{ post_rows }}", rows_html)
    html = html.replace("{{ post_count }}", str(len(posts)))
    html = html.replace("{{ built_at }}", datetime.now().strftime("%B %d, %Y"))
    return html


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    print("\n═══════════════════════════════════")
    print("  SterIndex — generate_blog.py")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("═══════════════════════════════════\n")

    if not POST_TEMPLATE_PATH.exists():
        print(f"✗ Missing template: {POST_TEMPLATE_PATH}")
        return
    if not INDEX_TEMPLATE_PATH.exists():
        print(f"✗ Missing template: {INDEX_TEMPLATE_PATH}")
        return

    post_template  = POST_TEMPLATE_PATH.read_text(encoding="utf-8")
    index_template = INDEX_TEMPLATE_PATH.read_text(encoding="utf-8")

    print("► Loading Markdown posts…")
    posts = load_all_posts()
    print(f"  {len(posts)} posts found (sorted newest → oldest)\n")

    for p in posts:
        print(f"  [{p['type']:7}] {p['date_display']}  —  {p['title']}")

    print("\n► Writing individual post pages…")
    for post in posts:
        html = render_post_page(post, post_template)
        out_path = OUTPUT_DIR / f"{post['slug']}.html"
        out_path.write_text(html, encoding="utf-8")
        print(f"  Wrote blog/{post['slug']}.html")

    print("\n► Writing blog index page…")
    index_html = render_index_page(posts, index_template)
    INDEX_OUTPUT.write_text(index_html, encoding="utf-8")
    print(f"  Wrote {INDEX_OUTPUT.name}")

    print(f"\n✓ Done! {len(posts)} posts published.\n")


if __name__ == "__main__":
    main()
