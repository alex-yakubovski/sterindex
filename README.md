# SterIndex — Full Site Package

## Files

| File | Purpose |
|---|---|
| `build.py` | Run to fetch RSS + generate all HTML |
| `requirements.txt` | Python deps: httpx, jinja2 |
| `producers.html` | Static page — 100 companies directory |
| `templates/index.html` | Jinja2 — paginated news index |
| `templates/article.html` | Jinja2 — individual article pages |
| `templates/legal.html` | Jinja2 — legal notice page |

## Generated on build
| File | Purpose |
|---|---|
| `index.html` | Latest 30 articles |
| `index2.html`, `index3.html` … | Older paginated pages |
| `articles/*.html` | One file per article |
| `legal.html` | Legal notice with current date |
| `archive.json` | Persistent article store |

## Quick start
```bash
# 1. Install Ollama + pull model
curl -fsSL https://ollama.com/install.sh | sh
ollama pull mistral

# 2. Install Python deps
pip install httpx jinja2

# 3. Build
python build.py

# 4. Preview
python -m http.server 8080

# 5. Deploy — push everything to GitHub Pages repo
git add . && git commit -m "Launch" && git push
```

## Before publishing
Replace these placeholders in templates and producers.html:
- YOUR_ABOUT_URL
- YOUR_PRIVACY_URL
- YOUR_CONTACT_URL
