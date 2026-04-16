#!/usr/bin/env python3
"""
reading_pipeline.py — Chrome Reading List → Content Generation Pipeline

Usage:
    python3 reading_pipeline.py scan                    # Extract & score reading list
    python3 reading_pipeline.py generate <url>          # Generate content for a URL
    python3 reading_pipeline.py generate --top 3        # Auto-pick top 3 compelling items
    python3 reading_pipeline.py list                    # Show reading list with scores
    python3 reading_pipeline.py publish <content_dir>   # Open generated content
    python3 reading_pipeline.py visual <title>          # Generate OG card image
"""

import argparse
import json
import os
import re
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

# ── Constants ────────────────────────────────────────────────────────────────

REPO_DIR = Path(__file__).resolve().parent
READING_LIST_JSON = REPO_DIR / "reading_list.json"
CONTENT_DIR = REPO_DIR / "content"
TEMPLATES_DIR = REPO_DIR / "templates"
ENV_FILE = REPO_DIR / ".env.json"

CHROME_BOOKMARKS = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Google"
    / "Chrome"
    / "Default"
    / "Bookmarks"
)

# Folders to treat as "read later" sources
READ_LATER_FOLDER_NAMES = {
    "read later",
    "reading list",
    "to read",
    "saved",
    "read",
}


# ── Config ───────────────────────────────────────────────────────────────────

def load_config():
    """Load Azure OpenAI config from .env.json."""
    if ENV_FILE.exists():
        with open(ENV_FILE) as f:
            return json.load(f)
    return {}


def get_ai_client():
    """Create Azure OpenAI client."""
    cfg = load_config()
    endpoint = cfg.get("AZURE_OPENAI_ENDPOINT") or os.environ.get("AZURE_OPENAI_ENDPOINT")
    key = cfg.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_KEY")
    api_version = cfg.get("AZURE_OPENAI_API_VERSION", "2024-06-01")

    if not endpoint or not key:
        return None, cfg

    try:
        from openai import AzureOpenAI

        client = AzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=api_version,
        )
        return client, cfg
    except Exception as e:
        print(f"⚠️  Azure OpenAI setup failed: {e}")
        return None, cfg


# ── Reading List Extraction ──────────────────────────────────────────────────

def extract_reading_list():
    """Extract items from Chrome's reading list and Read Later folders."""
    items = []

    if not CHROME_BOOKMARKS.exists():
        print(f"⚠️  Chrome bookmarks not found at {CHROME_BOOKMARKS}")
        return items

    with open(CHROME_BOOKMARKS) as f:
        data = json.load(f)

    roots = data.get("roots", {})

    # Method 1: Chrome's built-in reading_list
    reading_list = roots.get("reading_list", {})
    for child in reading_list.get("children", []):
        if child.get("type") == "url":
            items.append(
                {
                    "url": child.get("url", ""),
                    "title": child.get("name", ""),
                    "date_added": child.get("date_added", ""),
                    "read": child.get("read", False),
                    "source": "reading_list",
                }
            )

    # Method 2: Scan for "Read Later" / "Reading List" folders
    def scan_folders(node, path=""):
        if not isinstance(node, dict):
            return
        name = node.get("name", "")
        ntype = node.get("type", "")

        if ntype == "folder" and name.lower().strip() in READ_LATER_FOLDER_NAMES:
            for child in node.get("children", []):
                if child.get("type") == "url":
                    items.append(
                        {
                            "url": child.get("url", ""),
                            "title": child.get("name", ""),
                            "date_added": child.get("date_added", ""),
                            "read": False,
                            "source": f"folder:{path}/{name}",
                        }
                    )
            return

        for child in node.get("children", []):
            scan_folders(child, f"{path}/{name}" if name else path)

    for key, root_node in roots.items():
        if key != "reading_list":
            scan_folders(root_node)

    # Deduplicate by URL
    seen = set()
    unique = []
    for item in items:
        if item["url"] not in seen:
            seen.add(item["url"])
            unique.append(item)
    return unique


# ── Web Scraping ─────────────────────────────────────────────────────────────

def scrape_url(url):
    """Scrape article content from a URL."""
    try:
        import requests
        from bs4 import BeautifulSoup
    except ImportError:
        print("⚠️  Install dependencies: pip3 install beautifulsoup4 requests")
        return None

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️  Failed to fetch {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "html.parser")

    # Extract metadata
    title = ""
    if soup.title:
        title = soup.title.string or ""
    og_title = soup.find("meta", property="og:title")
    if og_title:
        title = og_title.get("content", title)

    description = ""
    meta_desc = soup.find("meta", attrs={"name": "description"})
    if meta_desc:
        description = meta_desc.get("content", "")
    og_desc = soup.find("meta", property="og:description")
    if og_desc:
        description = og_desc.get("content", description)

    og_image = ""
    og_img_tag = soup.find("meta", property="og:image")
    if og_img_tag:
        og_image = og_img_tag.get("content", "")

    author = ""
    author_meta = soup.find("meta", attrs={"name": "author"})
    if author_meta:
        author = author_meta.get("content", "")

    # Extract main content
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "iframe"]):
        tag.decompose()

    # Try article tag first, then main, then body
    content_el = soup.find("article") or soup.find("main") or soup.find("body")
    text = ""
    if content_el:
        paragraphs = content_el.find_all("p")
        text = "\n\n".join(p.get_text(strip=True) for p in paragraphs if len(p.get_text(strip=True)) > 30)

    # Extract headings for structure
    headings = []
    for h in soup.find_all(["h1", "h2", "h3"]):
        ht = h.get_text(strip=True)
        if ht and len(ht) > 3:
            headings.append(ht)

    return {
        "url": url,
        "title": title.strip(),
        "description": description.strip(),
        "author": author.strip(),
        "og_image": og_image,
        "text": text[:5000],  # Limit to 5000 chars
        "headings": headings[:15],
        "domain": urlparse(url).hostname or "",
        "word_count": len(text.split()),
    }


# ── AI Topic Scoring ────────────────────────────────────────────────────────

def score_topics(items, scraped_data):
    """Use AI to score topics for content potential."""
    client, cfg = get_ai_client()
    deployment = cfg.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    if not client:
        print("⚠️  Azure OpenAI not configured. Using heuristic scoring.")
        return heuristic_score(items, scraped_data)

    scored = []
    for item in items:
        data = scraped_data.get(item["url"], {})
        if not data:
            item["score"] = 0
            item["reason"] = "Could not scrape content"
            item["category"] = "unknown"
            item["angle"] = ""
            scored.append(item)
            continue

        prompt = f"""Analyze this article for content repurposing potential.

Title: {data.get('title', '')}
Description: {data.get('description', '')}
Domain: {data.get('domain', '')}
Headings: {', '.join(data.get('headings', [])[:8])}
Content preview (first 1500 chars): {data.get('text', '')[:1500]}

Rate this article on a scale of 0-100 for its potential to be repurposed into:
1. A Medium blog post
2. A LinkedIn post
3. A Twitter/X thread

Consider: relevance to cloud/AI/tech audience, uniqueness of insight, practical value, trending topics.

Respond in this exact JSON format:
{{"score": <0-100>, "category": "<tutorial|opinion|news|deep-dive|tool-review|case-study>", "reason": "<1 sentence why>", "angle": "<suggested unique angle for content creation>", "key_takeaways": ["<takeaway 1>", "<takeaway 2>", "<takeaway 3>"]}}"""

        try:
            response = client.chat.completions.create(
                model=deployment,
                messages=[
                    {"role": "system", "content": "You are a content strategist. Respond only with valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=300,
            )
            result = json.loads(response.choices[0].message.content)
            item.update(result)
        except Exception as e:
            print(f"  ⚠️  AI scoring failed for {item['url'][:60]}: {e}")
            item["score"] = 50
            item["reason"] = "AI scoring failed, default score"
            item["category"] = "unknown"
            item["angle"] = ""

        scored.append(item)
        print(f"  📊 {item.get('score', 0):3d}/100 | {item['title'][:60]}")

    return scored


def heuristic_score(items, scraped_data):
    """Fallback scoring without AI — uses content signals."""
    high_value_domains = {
        "github.com", "medium.com", "dev.to", "aws.amazon.com",
        "learn.microsoft.com", "cloud.google.com", "arxiv.org",
        "huggingface.co", "openai.com", "anthropic.com",
    }
    trending_keywords = [
        "ai", "llm", "gpt", "copilot", "agent", "rag", "vector",
        "kubernetes", "serverless", "terraform", "rust", "golang",
        "microservices", "devops", "mlops", "fine-tuning",
    ]

    for item in items:
        data = scraped_data.get(item["url"], {})
        score = 30  # Base score

        domain = data.get("domain", urlparse(item["url"]).hostname or "")
        if any(hv in domain for hv in high_value_domains):
            score += 20

        text = (data.get("title", "") + " " + data.get("description", "") + " " + data.get("text", "")).lower()
        keyword_hits = sum(1 for kw in trending_keywords if kw in text)
        score += min(keyword_hits * 5, 30)

        word_count = data.get("word_count", 0)
        if word_count > 500:
            score += 10
        if word_count > 1500:
            score += 10

        item["score"] = min(score, 100)
        item["reason"] = f"Heuristic: {keyword_hits} trending keywords, {word_count} words"
        item["category"] = "unknown"
        item["angle"] = ""
        print(f"  📊 {item['score']:3d}/100 | {item['title'][:60]}")

    return items


# ── Content Generation ───────────────────────────────────────────────────────

def generate_content(item, scraped, cfg):
    """Generate Medium blog, LinkedIn post, and Twitter thread."""
    client, cfg = get_ai_client()
    deployment = cfg.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")
    voice = cfg.get("CONTENT_VOICE", "Cloud & AI architect sharing practical insights")
    linkedin = cfg.get("LINKEDIN_HANDLE", "")
    twitter = cfg.get("TWITTER_HANDLE", "")

    date_str = datetime.now().strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", (item.get("title", "untitled")).lower())[:50].strip("-")
    content_path = CONTENT_DIR / date_str / slug
    content_path.mkdir(parents=True, exist_ok=True)

    context = f"""Source article:
Title: {scraped.get('title', '')}
URL: {item['url']}
Description: {scraped.get('description', '')}
Key headings: {', '.join(scraped.get('headings', [])[:8])}
Content: {scraped.get('text', '')[:3000]}
Suggested angle: {item.get('angle', '')}
Key takeaways: {json.dumps(item.get('key_takeaways', []))}"""

    results = {"path": str(content_path), "files": []}

    if client:
        # Generate Medium blog
        blog = _generate_with_ai(
            client,
            deployment,
            f"""Write a compelling Medium blog post (800-1200 words) based on this source material.

{context}

Voice/persona: {voice}

Requirements:
- SEO-optimized title (different from source)
- Hook intro paragraph
- 3-5 structured sections with H2 headings
- Practical insights and actionable takeaways
- Code snippets if relevant (use markdown fenced blocks)
- Conclusion with call-to-action
- Add source attribution at the end

Output as Markdown.""",
        )
        blog_file = content_path / "blog.md"
        blog_file.write_text(blog)
        results["files"].append("blog.md")
        print(f"  📝 Blog: {blog_file}")

        # Generate LinkedIn post
        linkedin_post = _generate_with_ai(
            client,
            deployment,
            f"""Create a LinkedIn post (200-300 words) based on this source material.

{context}

Voice/persona: {voice}
{f'LinkedIn: {linkedin}' if linkedin else ''}

Requirements:
- Strong hook line (question or bold statement)
- 3-5 key insights as short paragraphs
- Use line breaks between paragraphs for readability
- End with a question to drive engagement
- Add 3-5 relevant hashtags
- Use Unicode formatting: 𝗕𝗼𝗹𝗱 for emphasis (Unicode bold, not markdown)
- Include emoji sparingly (1-2 per paragraph max)
- Do NOT use markdown formatting — this is plain text for LinkedIn

Output the post text only, ready to copy-paste.""",
        )
        li_file = content_path / "linkedin.txt"
        li_file.write_text(linkedin_post)
        results["files"].append("linkedin.txt")
        print(f"  💼 LinkedIn: {li_file}")

        # Generate Twitter thread
        twitter_thread = _generate_with_ai(
            client,
            deployment,
            f"""Create a Twitter/X thread (6-8 tweets) based on this source material.

{context}

Voice/persona: {voice}
{f'Twitter: {twitter}' if twitter else ''}

Requirements:
- Tweet 1: Hook with bold claim or question (≤280 chars)
- Tweets 2-6: Key insights, one per tweet (≤280 chars each)
- Final tweet: Summary + link to source + CTA
- Number each tweet (1/, 2/, etc.)
- Include relevant hashtags in last tweet only
- Each tweet must be ≤280 characters

Output each tweet on its own line, separated by blank lines.""",
        )
        tw_file = content_path / "twitter.txt"
        tw_file.write_text(twitter_thread)
        results["files"].append("twitter.txt")
        print(f"  🐦 Twitter: {tw_file}")
    else:
        # Fallback: template-based generation
        blog = _template_blog(item, scraped, voice)
        (content_path / "blog.md").write_text(blog)
        results["files"].append("blog.md")

        li = _template_linkedin(item, scraped, voice)
        (content_path / "linkedin.txt").write_text(li)
        results["files"].append("linkedin.txt")

        tw = _template_twitter(item, scraped)
        (content_path / "twitter.txt").write_text(tw)
        results["files"].append("twitter.txt")

        print(f"  📝 Template-based content generated (configure Azure OpenAI for AI-powered)")
        print(f"  📁 {content_path}")

    # Save metadata
    meta = {
        "source_url": item["url"],
        "source_title": item.get("title", ""),
        "score": item.get("score", 0),
        "category": item.get("category", ""),
        "angle": item.get("angle", ""),
        "generated_at": datetime.now().isoformat(),
        "files": results["files"],
    }
    (content_path / "meta.json").write_text(json.dumps(meta, indent=2))

    return results


def _generate_with_ai(client, deployment, prompt):
    """Call Azure OpenAI to generate content."""
    try:
        response = client.chat.completions.create(
            model=deployment,
            messages=[
                {"role": "system", "content": "You are an expert content creator and technical writer."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.7,
            max_tokens=2000,
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"[AI generation failed: {e}]\n\nPlease configure Azure OpenAI in .env.json"


def _template_blog(item, scraped, voice):
    """Template-based blog post (no AI)."""
    title = scraped.get("title", item.get("title", "Untitled"))
    desc = scraped.get("description", "")
    headings = scraped.get("headings", [])
    text = scraped.get("text", "")[:2000]

    sections = ""
    for h in headings[:5]:
        sections += f"\n## {h}\n\n[Content to be expanded based on source material]\n"

    return f"""# {title}

> *{voice}*

{desc}

{sections if sections else f'''
## Key Insights

{text[:500]}

## Why This Matters

[Expand on the practical implications]

## Getting Started

[Add actionable steps]
'''}

## Conclusion

[Add your perspective and call-to-action]

---

*Source: [{title}]({item["url"]})*
*Generated on {datetime.now().strftime("%Y-%m-%d")} — edit before publishing*
"""


def _template_linkedin(item, scraped, voice):
    """Template-based LinkedIn post (no AI)."""
    title = scraped.get("title", item.get("title", ""))
    desc = scraped.get("description", "")
    return f"""🚀 {title}

{desc[:200]}

Here's what caught my attention:

📌 [Key insight 1]
📌 [Key insight 2]
📌 [Key insight 3]

What are your thoughts on this?

🔗 {item['url']}

#CloudArchitecture #AI #TechInsights
"""


def _template_twitter(item, scraped):
    """Template-based Twitter thread (no AI)."""
    title = scraped.get("title", item.get("title", ""))
    return f"""1/ 🧵 {title[:200]}

2/ [Key insight from the article]

3/ [Another interesting point]

4/ [Practical takeaway]

5/ [Your perspective]

6/ Full article: {item['url']}

#Tech #AI #CloudComputing
"""


# ── Visual Generation ────────────────────────────────────────────────────────

def generate_og_card(title, subtitle="", domain="", output_path=None):
    """Generate an OG card / social media image using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("⚠️  Install Pillow: pip3 install Pillow")
        return None

    W, H = 1200, 630
    img = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(img)

    # Gradient background (dark blue to purple)
    for y in range(H):
        r = int(15 + (45 - 15) * y / H)
        g = int(23 + (15 - 23) * y / H)
        b = int(42 + (80 - 42) * y / H)
        draw.line([(0, y), (W, y)], fill=(r, g, b))

    # Accent line at top
    draw.rectangle([(0, 0), (W, 6)], fill=(88, 166, 255))

    # Try to load a nice font, fallback to default
    def get_font(size):
        font_paths = [
            "/System/Library/Fonts/SFNSText.ttf",
            "/System/Library/Fonts/Helvetica.ttc",
            "/System/Library/Fonts/HelveticaNeue.ttc",
            "/Library/Fonts/Arial.ttf",
        ]
        for fp in font_paths:
            if os.path.exists(fp):
                try:
                    return ImageFont.truetype(fp, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    font_title = get_font(42)
    font_sub = get_font(24)
    font_domain = get_font(18)
    font_brand = get_font(20)

    # Wrap title text
    margin = 80
    max_chars = 40
    lines = textwrap.wrap(title, width=max_chars)[:3]

    y_pos = 120
    for line in lines:
        draw.text((margin, y_pos), line, fill=(230, 237, 243), font=font_title)
        y_pos += 56

    # Subtitle
    if subtitle:
        sub_lines = textwrap.wrap(subtitle, width=60)[:2]
        y_pos += 20
        for line in sub_lines:
            draw.text((margin, y_pos), line, fill=(139, 148, 158), font=font_sub)
            y_pos += 34

    # Domain badge
    if domain:
        y_pos = H - 100
        draw.rounded_rectangle(
            [(margin, y_pos), (margin + len(domain) * 11 + 24, y_pos + 32)],
            radius=16,
            fill=(31, 58, 95),
        )
        draw.text((margin + 12, y_pos + 6), f"🌐 {domain}", fill=(88, 166, 255), font=font_domain)

    # Brand
    draw.text((W - 280, H - 50), "🔖 My Bookmarks", fill=(110, 118, 129), font=font_brand)

    # Decorative dots
    for i in range(5):
        x = W - 100 + i * 15
        draw.ellipse([(x, 30), (x + 8, 38)], fill=(88, 166, 255, 80))

    if output_path is None:
        output_path = CONTENT_DIR / "og_card.png"
    else:
        output_path = Path(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(output_path), "PNG", quality=95)
    print(f"  🎨 OG Card: {output_path}")
    return str(output_path)


# ── Reading List Management ──────────────────────────────────────────────────

def load_reading_list():
    """Load reading list from JSON file."""
    if READING_LIST_JSON.exists():
        with open(READING_LIST_JSON) as f:
            return json.load(f)
    return {"items": [], "last_scan": None}


def save_reading_list(data):
    """Save reading list to JSON file."""
    with open(READING_LIST_JSON, "w") as f:
        json.dump(data, f, indent=2)


def mark_processed(url):
    """Mark a reading list item as processed."""
    data = load_reading_list()
    for item in data["items"]:
        if item["url"] == url:
            item["processed"] = True
            item["processed_at"] = datetime.now().isoformat()
    save_reading_list(data)


# ── CLI Commands ─────────────────────────────────────────────────────────────

def cmd_scan(args):
    """Scan reading list, scrape content, and score topics."""
    print("🔍 Scanning Chrome Reading List...")
    items = extract_reading_list()

    if not items:
        print("\n⚠️  No items found in Chrome Reading List or 'Read Later' folders.")
        print("   To use this feature:")
        print("   1. Add items to Chrome's Reading List (right-click → 'Add to Reading List')")
        print("   2. Or create a bookmark folder named 'Read Later'")
        return

    print(f"📚 Found {len(items)} items\n")

    # Scrape content
    print("🌐 Scraping article content...")
    scraped_data = {}
    for item in items:
        print(f"  ↳ {item['title'][:60]}...")
        data = scrape_url(item["url"])
        if data:
            scraped_data[item["url"]] = data

    # Score topics
    print(f"\n📊 Scoring {len(items)} topics...")
    scored = score_topics(items, scraped_data)
    scored.sort(key=lambda x: x.get("score", 0), reverse=True)

    # Save
    reading_data = {
        "items": scored,
        "scraped": scraped_data,
        "last_scan": datetime.now().isoformat(),
    }
    save_reading_list(reading_data)

    # Display results
    print(f"\n{'─' * 70}")
    print(f"{'Score':>5}  {'Category':<12}  Title")
    print(f"{'─' * 70}")
    for item in scored:
        score = item.get("score", 0)
        cat = item.get("category", "?")[:12]
        stars = "🔥" if score >= 80 else "⭐" if score >= 60 else "  "
        print(f" {score:3d}   {cat:<12}  {stars} {item['title'][:55]}")
    print(f"{'─' * 70}")
    print(f"\n💾 Saved to {READING_LIST_JSON}")
    print(f"💡 Run: python3 reading_pipeline.py generate --top 3")


def cmd_list(args):
    """Display current reading list with scores."""
    data = load_reading_list()
    items = data.get("items", [])

    if not items:
        print("📭 Reading list is empty. Run 'scan' first.")
        return

    print(f"\n📚 Reading List ({len(items)} items)")
    print(f"   Last scan: {data.get('last_scan', 'never')}\n")
    print(f"{'Score':>5}  {'Status':<10}  Title")
    print(f"{'─' * 70}")
    for item in sorted(items, key=lambda x: x.get("score", 0), reverse=True):
        score = item.get("score", 0)
        status = "✅ done" if item.get("processed") else "📋 pending"
        print(f" {score:3d}   {status:<10}  {item['title'][:55]}")
    print()


def cmd_generate(args):
    """Generate content for reading list items."""
    data = load_reading_list()
    items = data.get("items", [])
    scraped = data.get("scraped", {})
    cfg = load_config()

    if not items:
        print("📭 Reading list is empty. Run 'scan' first.")
        return

    targets = []
    if args.url:
        # Specific URL
        target = next((i for i in items if i["url"] == args.url), None)
        if not target:
            print(f"⚠️  URL not found in reading list: {args.url}")
            return
        targets = [target]
    elif args.top:
        # Top N unprocessed items by score
        unprocessed = [i for i in items if not i.get("processed")]
        unprocessed.sort(key=lambda x: x.get("score", 0), reverse=True)
        targets = unprocessed[: args.top]
    else:
        print("⚠️  Specify --url <url> or --top <n>")
        return

    if not targets:
        print("✅ All items already processed!")
        return

    print(f"\n🚀 Generating content for {len(targets)} item(s)...\n")

    for item in targets:
        print(f"{'─' * 60}")
        print(f"📰 {item['title'][:60]}")
        print(f"   Score: {item.get('score', '?')}/100 | {item.get('category', '?')}")

        article_data = scraped.get(item["url"])
        if not article_data:
            print("  ↳ Scraping article...")
            article_data = scrape_url(item["url"])
            if not article_data:
                print("  ⚠️  Could not scrape article. Skipping.")
                continue

        # Generate content
        result = generate_content(item, article_data, cfg)

        # Generate OG card
        og_path = Path(result["path"]) / "og_card.png"
        generate_og_card(
            title=item.get("title", ""),
            subtitle=item.get("angle", item.get("reason", "")),
            domain=urlparse(item["url"]).hostname or "",
            output_path=og_path,
        )

        # Mark as processed
        mark_processed(item["url"])
        print(f"  ✅ Content saved to {result['path']}")

    print(f"\n{'─' * 60}")
    print(f"🎉 Done! Generated content for {len(targets)} article(s)")
    print(f"📂 Content directory: {CONTENT_DIR}")


def cmd_visual(args):
    """Generate a standalone OG card."""
    title = args.title
    subtitle = args.subtitle or ""
    domain = args.domain or ""
    output = args.output or str(CONTENT_DIR / "og_card.png")

    print(f"🎨 Generating OG card...")
    path = generate_og_card(title, subtitle, domain, output)
    if path:
        print(f"✅ Saved: {path}")
        if sys.platform == "darwin":
            os.system(f"open '{path}'")


def cmd_publish(args):
    """Open generated content for review."""
    content_dir = Path(args.path) if args.path else CONTENT_DIR
    if not content_dir.exists():
        print(f"⚠️  Content directory not found: {content_dir}")
        return

    # Find latest content
    dirs = sorted(content_dir.rglob("meta.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not dirs:
        print("📭 No generated content found.")
        return

    for meta_path in dirs[:5]:
        with open(meta_path) as f:
            meta = json.load(f)
        print(f"\n📰 {meta.get('source_title', '?')}")
        print(f"   Score: {meta.get('score', '?')} | {meta.get('category', '?')}")
        print(f"   Files: {', '.join(meta.get('files', []))}")
        print(f"   Path:  {meta_path.parent}")

    if sys.platform == "darwin":
        latest = dirs[0].parent
        os.system(f"open '{latest}'")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="📖 Reading List → Content Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python3 reading_pipeline.py scan               # Extract & score reading list
              python3 reading_pipeline.py list                # Show scored items
              python3 reading_pipeline.py generate --top 3    # Generate for top 3
              python3 reading_pipeline.py generate --url URL  # Generate for specific URL
              python3 reading_pipeline.py visual "My Title"   # Create OG card
              python3 reading_pipeline.py publish             # Open latest content
        """),
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="Extract & score reading list items")
    sub.add_parser("list", help="Show reading list with scores")

    gen = sub.add_parser("generate", help="Generate content")
    gen.add_argument("--url", help="Generate for specific URL")
    gen.add_argument("--top", type=int, help="Generate for top N items")

    vis = sub.add_parser("visual", help="Generate OG card image")
    vis.add_argument("title", help="Card title")
    vis.add_argument("--subtitle", help="Card subtitle")
    vis.add_argument("--domain", help="Source domain")
    vis.add_argument("--output", help="Output file path")

    pub = sub.add_parser("publish", help="Open generated content")
    pub.add_argument("path", nargs="?", help="Content directory path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    commands = {
        "scan": cmd_scan,
        "list": cmd_list,
        "generate": cmd_generate,
        "visual": cmd_visual,
        "publish": cmd_publish,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
