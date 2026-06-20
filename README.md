# SterIndex — Complete Website Package

Sterile Technology Intelligence platform. Static site, two independent
build scripts, ready for GitHub Pages + custom domain (sterindex.com).

---

## Full File Structure

```
sterindex/
├── build.py                          ← News + Producers + Companies builder
├── generate_blog.py                  ← Blog/Digest publishing builder
├── requirements.txt
├── CNAME                             ← custom domain config (add before push)
│
├── about.html                        ← static page
├── privacy.html                      ← static page
├── contact.html                      ← static page (Formspree form)
├── producers.html                    ← static page (100-company directory)
│
├── templates/                        ← Jinja2 templates (used by build.py)
│   ├── index.html                    ← news index (paginated)
│   ├── article.html                  ← individual news article
│   ├── legal.html                    ← legal notice
│   └── company.html                  ← vendor profile page
│   ├── post_template.html            ← used by generate_blog.py
│   └── blog_index_template.html      ← used by generate_blog.py
│
└── content/                          ← Markdown source for blog
    ├── articles/
    │   └── *.md
    └── digests/
        └── *.md
```

## Generated on build (not included — created by running the scripts)

```
index.html, index2.html, ...   ← from build.py (news, paginated)
articles/*.html                ← from build.py (news articles)
companies/*.html                ← from build.py (100 vendor profiles)
legal.html                      ← from build.py
blog.html                       ← from generate_blog.py
blog/*.html                     ← from generate_blog.py (posts)
archive.json                    ← from build.py (persistent article store)
```

---

## One-Time Setup

```bash
# 1. Install Ollama (free local AI for news summaries)
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral

# 2. Install Python dependencies
pip install -r requirements.txt
```

## Running the Site

```bash
# Generate news, producers directory, company profiles, legal page
python build.py

# Generate blog index + individual posts from Markdown
python generate_blog.py
```

Run both scripts any time to refresh content. Both are safe to re-run —
old content is never deleted, only new content is added.

## Local Preview

```bash
python -m http.server 8080
# open http://localhost:8080
```

---

## Deploying to sterindex.com (Custom Domain on GitHub Pages)

### 1. Add a CNAME file to the repo root
Create a file named exactly `CNAME` (no extension) containing only:
```
sterindex.com
```

### 2. Configure DNS at your domain registrar
Add these records at wherever sterindex.com is registered:

**For apex domain (sterindex.com):**
```
Type: A
Host: @
Value: 185.199.108.153
Value: 185.199.109.153
Value: 185.199.110.153
Value: 185.199.111.153
```

**For www subdomain (optional, recommended):**
```
Type: CNAME
Host: www
Value: yourusername.github.io
```

### 3. Enable in GitHub
Repo → **Settings → Pages → Custom domain** → enter `sterindex.com` → Save.
Check **Enforce HTTPS** once GitHub issues the SSL certificate (can take
up to 24 hours).

### 4. Push everything
```bash
git add .
git commit -m "Launch SterIndex on custom domain"
git push
```

DNS propagation can take anywhere from a few minutes to 48 hours.

---

## Contact Form

`contact.html` uses Formspree (form ID: `mojzpwwg`). Submissions are
emailed to whatever address was used to register that Formspree account.
To change it, create a new form at formspree.io and update the `action`
URL in `contact.html`.

## Adding New Blog Posts

Drop a `.md` file into `content/articles/` or `content/digests/`:
```markdown
---
title: Your Post Title
date: 2026-07-01
type: Article
---

Your content here in Markdown.
```
Run `python generate_blog.py` — it re-sorts everything by date and
regenerates the blog index automatically.

## Adding New Producer Companies

Edit the `COMPANIES` list directly inside `build.py`. Add to
`VERIFIED_COMPANIES` set to grant the green verified badge. Run
`python build.py` to regenerate the directory and individual profile pages.
