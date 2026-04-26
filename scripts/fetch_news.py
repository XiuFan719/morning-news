#!/usr/bin/env python3
"""
Daily Briefing Aggregator

Fetches the past 24h of hot content from:
  - X/Twitter (via bird-search Node.js subprocess)
  - Hacker News (via Algolia API)
  - Reddit (via Reddit JSON API, /r/all/hot)
  - Science feeds (Nature, Science Daily, Science.org via RSS with proxy)
  - Domestic China news (iTHome via RSS, no proxy)

Categorizes into AI / Politics / Tech / General / Science / China.
Merges duplicates across sources. Outputs structured JSON to stdout.

Usage:
  export PATH="$HOME/local/node/bin:$PATH"
  export http_proxy="http://127.0.0.1:7890"
  export https_proxy="http://127.0.0.1:7890"
  export AUTH_TOKEN="..."
  export CT0="..."
  export CRON_RUN=1   # only set during cron execution to persist seen-stories
  python3 daily_x_briefing.py > /tmp/raw_briefing.json
"""

import json
import os
import re
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone, timedelta

# ============================================================
# Config
# ============================================================

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BIRD_SEARCH_DIR = os.path.expanduser(
    "~/.hermes/skills/news/daily-news-briefing/scripts/bird-search"
)
SEEN_FILE = os.path.expanduser("~/.hermes/scripts/.seen_stories.json")
SEEN_RETENTION_DAYS = 7

LOOKBACK_HOURS = 24
LOOKBACK_HOURS_REDDIT = 24  # Reddit hot sort is time-agnostic, filter client-side

# Top mode gives the best quality (high engagement) results within 24h
MIN_X_LIKES = 100  # tweets with < 100 likes are probably noise/promotion
MAX_ITEMS_PER_CATEGORY = 15  # cap output size per category

# Marketing/promotion patterns to filter out
SPAM_PATTERNS = [
    r'\b\d+%\s*OFF\b', r'\bdiscount\b', r'\bdon\'?t miss\b', r'\blimited time\b',
    r'\bsubscribe\b', r'\bsign up\b', r'\bfree\s+trial\b', r'\bpromo code\b',
    r'\bsponsored\b', r'\baffiliate\b', r'\bclick here\b', r'\bbuy now\b',
    r'\bcheck out\s+this\b', r'\bcheck out\s+the\b',
    r'\bsave\s+\d+%\b', r'\bmastering\s+(Claude|ChatGPT|AI)\b',
    r'\bnetflix\s+tonight\b', r'\bwatch\s+netflix\b',
]

def is_spam(text):
    text_lower = text.lower()
    for pat in SPAM_PATTERNS:
        if re.search(pat, text_lower):
            return True
    return False

X_QUERIES = [
    # Political/Military big events — weighted highest, fetched most
    ("Trump OR Biden OR US politics OR breaking news", "politics", 20),
    ("war OR military OR conflict OR missile OR strike", "politics", 15),
    ("China OR Taiwan OR South China Sea OR Philippines", "politics", 12),
    ("Russia OR Ukraine OR NATO OR Iran OR Israel", "politics", 12),
    ("politics OR geopolitics OR election OR trade tariff", "politics", 12),
    # AI/Tech
    ("AI OR artificial intelligence OR LLM OR GPT OR OpenAI", "ai", 10),
    ("technology OR tech startup OR chip OR semiconductor", "tech", 10),
    # Science/General
    ("science OR space OR NASA OR medical breakthrough", "general", 8),
    ("OpenAI OR Anthropic OR DeepSeek OR Claude OR Gemini", "ai", 8),
]

HN_QUERIES = [
    ("AI", "ai"),
    ("machine learning", "ai"),
    ("politics", "politics"),
    ("technology", "tech"),
    ("startup", "tech"),
    ("science", "general"),
]

REDDIT_SUBREDDITS = [
    # News subreddits only
    "all",
    "worldnews",
    "technology",
    "science",
    "artificial",
    "politics",
]

SCIENCE_FEEDS = [
    ("nature", "https://www.nature.com/nature.rss"),
    ("science_daily", "https://www.sciencedaily.com/rss/all.xml"),
    ("science_org", "https://www.science.org/rss/news_current.xml"),
]

CHINA_NEWS_FEEDS = [
    ("ithome", "https://www.ithome.com/rss/"),
]

CATEGORY_ORDER = ["ai", "politics", "tech", "general", "entertainment"]
NEWS_CATEGORY_ORDER = ["ai", "politics", "tech", "general"]  # only news, no entertainment

# Politics checked BEFORE AI — see skill doc pitfalls
POLITICS_KEYWORDS = [
    "trump", "biden", "election", "vote", "president", "congress",
    "senate", "policy", "war", "military", "conflict", "iran",
    "china", "russia", "ukraine", "israel", "gaza", "taiwan",
    "diplomacy", "sanction", "nato", "geopolitic", "tariff", "trade",
    "hezbollah", "netanyahu", "blockade", "ceasefire", "strike",
    "missile", "gop", "senator", "supreme court", "tariff",
    "indict", "impeach", "embargo", "refugee", "migrant",
    "g7", "united nations", "defense", "nuclear", "weapon",
    "protest", "sanction", "diplomat", "border", "citizen",
    "foreign", "ambassador", "parliament", "legislation",
    "govern", "administration", "white house", "kremlin",
    "pentagon", "congressional", "democrat", "republican",
    "prime minister", "presidential", "electoral",
]

AI_KEYWORDS = [
    "ai", "artificial intelligence", "llm", "gpt", "chatgpt", "openai",
    "machine learning", "deep learning", "neural", "model training",
    "inference", "claude", "x.ai", "grok", "open source model", "agent",
    "multimodal", "deepseek", "kimi", "transformer", "diffusion",
    "rag", "fine-tuning", "token", "copilot", "generative",
]

TECH_KEYWORDS = [
    "tech", "technology", "startup", "software", "app", "coding",
    "programming", "developer", "chip", "semiconductor", "iphone",
    "android", "gpu", "cloud", "open source", "github",
    "github copilot", "mcp", "container", "kubernetes", "docker",
    "blockchain", "crypto", "bitcoin", "cybersecurity", "hack",
    "quantum", "robot", "drone", "ev", "electric vehicle",
    "spacex", "space", "nasa", "satellite", "5g", "6g",
    "apple", "google", "microsoft", "meta", "amazon", "tesla",
    "nvidia", "intel", "amd", "qualcomm", "tsmc", "samsung",
]

ENTERTAINMENT_KEYWORDS = [
    "movie", "film", "tv show", "television", "comedy", "humor",
    "funny", "meme", "joke", "viral", "celebrity", "actor",
    "actress", "music", "song", "album", "concert", "gossip",
    "reality tv", "netflix", "disney", "marvel", "dc", "star wars",
    "oscar", "emmy", "grammy", "box office", "trailer",
    "tiktok", "youtube", "streamer", "influencer", "game show",
]

ENTERTAINMENT_SUBREDDITS = []  # No longer used; entertainment comes from Baidu Hot Search


# ============================================================
# Helpers
# ============================================================

def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def load_x_cookies():
    """Load AUTH_TOKEN and CT0 from daily-news-briefing .env file."""
    env_path = os.path.expanduser("~/.config/daily-news-briefing/.env")
    try:
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip())
    except (FileNotFoundError, IOError):
        eprint(f"⚠ X cookie file not found at {env_path}")


def load_seen_stories():
    try:
        with open(SEEN_FILE) as f:
            data = json.load(f)
            return data.get("stories", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_seen_stories(seen):
    cutoff = time.time() - SEEN_RETENTION_DAYS * 86400
    pruned = {}
    for day_key, ids in seen.items():
        if day_key.startswith("_"):
            continue
        try:
            if datetime.strptime(day_key, "%Y-%m-%d").timestamp() >= cutoff:
                pruned[day_key] = ids
        except ValueError:
            pruned[day_key] = ids
    pruned["_updated"] = int(time.time())
    os.makedirs(os.path.dirname(SEEN_FILE), exist_ok=True)
    tmp = SEEN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"stories": pruned}, f)
    os.replace(tmp, SEEN_FILE)


def parse_twitter_time(ts_str):
    try:
        dt = datetime.strptime(ts_str, "%a %b %d %H:%M:%S %z %Y")
        return int(dt.timestamp())
    except Exception:
        return 0


def categorize_text(text, default_cat="general"):
    text_lower = text.lower()
    if any(k in text_lower for k in POLITICS_KEYWORDS):
        return "politics"
    if any(k in text_lower for k in AI_KEYWORDS):
        return "ai"
    if any(k in text_lower for k in TECH_KEYWORDS):
        return "tech"
    if any(k in text_lower for k in ENTERTAINMENT_KEYWORDS):
        return "entertainment"
    return default_cat


def is_new_story(story_id, seen_set, seen_dict, today):
    if story_id in seen_set:
        return False
    today_ids = seen_dict.setdefault(today, [])
    today_ids.append(story_id)
    return True


def get_engagement(item):
    return item.get("likes", 0) or item.get("points", 0) or 0


# ============================================================
# X/Twitter via bird-search
# ============================================================

def run_bird_search(query, count=15):
    env = os.environ.copy()
    env.setdefault("PATH", os.path.expanduser("~/local/node/bin"))
    env.setdefault("http_proxy", "http://127.0.0.1:7890")
    env.setdefault("https_proxy", "http://127.0.0.1:7890")

    try:
        result = subprocess.run(
            ["node", "bird-search.mjs", query, "--count", str(count), "--json"],
            capture_output=True, text=True, timeout=30,
            env=env, cwd=BIRD_SEARCH_DIR,
        )
        if result.returncode != 0:
            eprint(f"bird-search error for '{query}': {result.stderr[:200]}")
            return []
        tweets = json.loads(result.stdout)
        if not isinstance(tweets, list):
            return []
        return tweets
    except json.JSONDecodeError as e:
        eprint(f"JSON parse error for '{query}': {e}")
        return []
    except subprocess.TimeoutExpired:
        eprint(f"bird-search timeout for '{query}'")
        return []
    except Exception as e:
        eprint(f"bird-search exception for '{query}': {e}")
        return []


def fetch_x_news():
    now_ts = int(time.time())
    since_ts = now_ts - LOOKBACK_HOURS * 3600
    items = []

    for query, default_cat, count in X_QUERIES:
        tweets = run_bird_search(query, count)
        for t in tweets:
            text = t.get("text", "")
            if not text:
                continue
            created = t.get("createdAt", "")
            ts = parse_twitter_time(created)
            if ts < since_ts:
                continue

            likes = t.get("likeCount", 0)
            if likes < MIN_X_LIKES:
                continue

            # Filter out marketing/promotional content
            if is_spam(text):
                continue

            author = t.get("author", {})
            item = {
                "id": t.get("id", str(ts)),
                "source": "x",
                "title": text,
                "author": author.get("username", ""),
                "author_name": author.get("name", ""),
                "url": f"https://x.com/{author.get('username', '')}/status/{t.get('id', '')}",
                "likes": likes,
                "retweets": t.get("retweetCount", 0),
                "replies": t.get("replyCount", 0),
                "category": categorize_text(text, default_cat),
                "created_at": created,
                "timestamp": ts,
            }
            items.append(item)
    return items


# ============================================================
# Hacker News via Algolia API
# ============================================================

def fetch_hn_news():
    now_ts = int(time.time())
    since_ts = now_ts - LOOKBACK_HOURS * 3600
    items = []
    proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")

    for query, default_cat in HN_QUERIES:
        url = (
            f"https://hn.algolia.com/api/v1/search_by_date"
            f"?query={urllib.request.quote(query)}"
            f"&tags=story"
            f"&numericFilters=created_at_i%3E{since_ts}"
            f"&hitsPerPage=15"
        )
        try:
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Accept": "application/json",
                }
            )
            if proxy:
                proxy_handler = urllib.request.ProxyHandler({
                    "http": proxy, "https": proxy
                })
                opener = urllib.request.build_opener(proxy_handler)
                urllib.request.install_opener(opener)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            for hit in data.get("hits", []):
                title = hit.get("title", "")
                if not title:
                    continue
                item = {
                    "id": f"hn_{hit.get('objectID', '')}",
                    "source": "hn",
                    "title": title,
                    "author": hit.get("author", ""),
                    "url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}",
                    "points": hit.get("points", 0),
                    "comments": hit.get("num_comments", 0),
                    "category": categorize_text(title, default_cat),
                    "created_at": hit.get("created_at", ""),
                    "timestamp": hit.get("created_at_i", 0),
                }
                items.append(item)
            time.sleep(0.3)
        except Exception as e:
            eprint(f"HN API error for '{query}': {e}")
    return items


# ============================================================
# Reddit via JSON API
# ============================================================

def fetch_reddit_news():
    now_ts = int(time.time())
    since_ts = now_ts - LOOKBACK_HOURS_REDDIT * 3600
    items = []
    proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy")

    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json",
    }

    for subreddit in REDDIT_SUBREDDITS:
        limit = 25
        local_since = since_ts
        url = f"https://www.reddit.com/r/{subreddit}/hot.json?limit={limit}"
        try:
            req = urllib.request.Request(url, headers=headers)
            if proxy:
                proxy_handler = urllib.request.ProxyHandler({
                    "http": proxy, "https": proxy
                })
                opener = urllib.request.build_opener(proxy_handler)
                urllib.request.install_opener(opener)
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            for child in data.get("data", {}).get("children", []):
                post = child.get("data", {})
                created = post.get("created_utc", 0)
                if created < local_since:
                    continue

                title = post.get("title", "")
                if not title:
                    continue

                sub = post.get("subreddit", "").lower()
                default_cat = "general"
                if sub in ("worldnews", "politics"):
                    default_cat = "politics"
                elif sub in ("technology",):
                    default_cat = "tech"
                elif sub in ("science",):
                    default_cat = "general"
                elif sub in ("artificial",):
                    default_cat = "ai"
                elif sub in ENTERTAINMENT_SUBREDDITS:
                    default_cat = "entertainment"
                cat = categorize_text(title, default_cat)

                item = {
                    "id": f"reddit_{post.get('id', '')}",
                    "source": "reddit",
                    "subreddit": post.get("subreddit", ""),
                    "title": title,
                    "author": post.get("author", ""),
                    "selftext": (post.get("selftext", "") or "")[:200],
                    "url": post.get("url") or f"https://reddit.com{post.get('permalink', '')}",
                    "permalink": f"https://reddit.com{post.get('permalink', '')}",
                    "score": post.get("score", 0),
                    "upvote_ratio": post.get("upvote_ratio", 0),
                    "comments": post.get("num_comments", 0),
                    "category": cat,
                    "created_at": datetime.fromtimestamp(created, tz=timezone.utc).isoformat(),
                    "timestamp": created,
                }
                items.append(item)
            time.sleep(0.3)
        except Exception as e:
            eprint(f"Reddit API error for r/{subreddit}: {e}")
    return items


# ============================================================
# RSS Feeds (Science + China)
# ============================================================

def fetch_rss_feed(feed_name, feed_url, use_proxy=True, max_items=3):
    try:
        proxy = os.environ.get("https_proxy") or os.environ.get("http_proxy") if use_proxy else None
        req = urllib.request.Request(
            feed_url,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        if proxy:
            proxy_handler = urllib.request.ProxyHandler({
                "http": proxy, "https": proxy
            })
            opener = urllib.request.build_opener(proxy_handler)
            urllib.request.install_opener(opener)
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
        root = ET.fromstring(raw)

        items = []
        # RSS format: channel -> item
        for item in root.iter("item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pubdate = item.findtext("pubDate", "")
            desc = item.findtext("description", "")
            items.append({
                "title": title, "url": link, "date": pubdate,
                "desc": desc[:300] if desc else "", "source": feed_name
            })

        # Atom format: entry
        if not items:
            ns = {"atom": "http://www.w3.org/2005/Atom"}
            for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
                title = entry.findtext("atom:title", "", ns)
                link_el = entry.find("atom:link", ns)
                link = link_el.get("href", "") if link_el is not None else ""
                published = entry.findtext("atom:published", "", ns)
                summary = entry.findtext("atom:summary", "", ns)
                items.append({
                    "title": title, "url": link, "date": published,
                    "desc": summary[:300] if summary else "", "source": feed_name
                })

        # RDF format (Nature)
        if not items:
            for item in root.iter("{http://purl.org/rss/1.0/}item"):
                title_el = item.find("{http://purl.org/rss/1.0/}title")
                link_el = item.find("{http://purl.org/rss/1.0/}link")
                desc_el = item.find("{http://purl.org/rss/1.0/}description")
                title = title_el.text if title_el is not None else ""
                link = link_el.text if link_el is not None else ""
                desc = desc_el.text if desc_el is not None else ""
                items.append({
                    "title": title, "url": link, "date": "",
                    "desc": desc[:300] if desc else "", "source": feed_name
                })

        return [it for it in items if it["title"] and
                "author correction" not in it["title"].lower() and
                "correction:" not in it["title"].lower()
               ][:max_items]
    except ET.ParseError as e:
        eprint(f"RSS parse error '{feed_name}': {e}")
        return []
    except Exception as e:
        eprint(f"RSS fetch error '{feed_name}': {e}")
        return []


def fetch_science_news():
    all_items = []
    for name, url in SCIENCE_FEEDS:
        items = fetch_rss_feed(name, url, use_proxy=True, max_items=3)
        all_items.extend(items)
    return all_items


def fetch_china_news():
    all_items = []
    for name, url in CHINA_NEWS_FEEDS:
        items = fetch_rss_feed(name, url, use_proxy=False, max_items=5)
        all_items.extend(items)
    return all_items


# ============================================================
# Baidu Hot Search (for domestic entertainment/social trends)
# ============================================================

BAIDU_HOT_URL = "https://top.baidu.com/api/board?tab=realtime"

def fetch_baidu_hot():
    """Fetch Baidu Hot Search (国内热搜/娱乐热点). No proxy needed."""
    items = []
    try:
        req = urllib.request.Request(
            BAIDU_HOT_URL,
            headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        cards = data.get("data", {}).get("cards", [])
        for card in cards:
            content = card.get("content", [])
            for entry in content:
                title = entry.get("word", "")
                desc = entry.get("desc", "")
                url = entry.get("url", "")
                hot_score = entry.get("hotScore", "0")
                if not title:
                    continue
                items.append({
                    "title": title,
                    "desc": desc[:200] if desc else "",
                    "url": url,
                    "hot_score": int(hot_score) if hot_score.isdigit() else 0,
                    "source": "baidu_hot",
                })
        return items
    except Exception as e:
        eprint(f"Baidu Hot API error: {e}")
        return []


# ============================================================
# Dedup: merge same stories from different sources
# ============================================================

def normalize_title(title):
    s = title.lower().strip()
    s = re.sub(r'[^a-z0-9\u4e00-\u9fff\s]', '', s)
    s = re.sub(r'\s+', ' ', s)
    return s[:80]


def dedup_items(items):
    seen_titles = {}
    for item in items:
        title_text = item.get("title", item.get("selftext", ""))
        norm = normalize_title(title_text)
        is_dup = False
        for existing_norm in seen_titles:
            short, long = sorted([norm, existing_norm], key=len)
            if short and (long.startswith(short) or short.startswith(long)):
                is_dup = True
                existing_item = seen_titles[existing_norm]
                if get_engagement(item) > get_engagement(existing_item):
                    seen_titles[existing_norm] = item
                break
        if not is_dup:
            seen_titles[norm] = item
    return list(seen_titles.values())


# ============================================================
# Main
# ============================================================

def main():
    eprint("=" * 60)
    eprint(f"Daily Briefing Aggregator — {datetime.now().isoformat()}")
    eprint("=" * 60)

    # Load X cookies from last30days .env
    load_x_cookies()

    # Load seen stories
    seen = load_seen_stories()
    today = time.strftime("%Y-%m-%d")
    all_seen_ids_set = set()
    for day_key, ids in seen.items():
        if day_key.startswith("_"):
            continue
        all_seen_ids_set.update(ids)

    all_items = []
    source_counts = {}

    # 1. X/Twitter
    eprint("\n[1/5] Fetching X/Twitter...")
    x_items = fetch_x_news()
    eprint(f"  -> {len(x_items)} items")
    all_items.extend(x_items)
    source_counts["x"] = len(x_items)

    # 2. Hacker News
    eprint("\n[2/5] Fetching Hacker News...")
    hn_items = fetch_hn_news()
    eprint(f"  -> {len(hn_items)} items")
    all_items.extend(hn_items)
    source_counts["hn"] = len(hn_items)

    # 3. Reddit
    eprint("\n[3/5] Fetching Reddit...")
    reddit_items = fetch_reddit_news()
    eprint(f"  -> {len(reddit_items)} items")
    all_items.extend(reddit_items)
    source_counts["reddit"] = len(reddit_items)

    # 4. Science RSS
    eprint("\n[4/5] Fetching Science RSS...")
    science_items = fetch_science_news()
    eprint(f"  -> {len(science_items)} items")
    source_counts["science"] = len(science_items)

    # 5. China domestic RSS
    eprint("\\n[5/5] Fetching China domestic news...")
    china_items = fetch_china_news()
    eprint(f"  -> {len(china_items)} items")
    source_counts["china"] = len(china_items)

    # 6. Baidu Hot Search (国内娱乐/社会热点)
    eprint("\\n[6/6] Fetching Baidu Hot Search...")
    baidu_hot = fetch_baidu_hot()
    eprint(f"  -> {len(baidu_hot)} items")
    source_counts["baidu_hot"] = len(baidu_hot)

    # Dedup categorized items (X + HN + Reddit)
    categorized_items = [it for it in all_items if it.get("source") in ("x", "hn", "reddit")]
    categorized_items = dedup_items(categorized_items)

    # Separate new vs repeated
    new_items = []
    repeated_items = []
    for item in categorized_items:
        story_id = str(item.get("id", item.get("url", "")))
        if is_new_story(story_id, all_seen_ids_set, seen, today):
            new_items.append(item)
        else:
            repeated_items.append(item)

    # Group new items by category
    by_category = defaultdict(list)
    for item in new_items:
        cat = item.get("category", "general")
        by_category[cat].append(item)

    # Sort within categories by engagement descending
    for cat in by_category:
        by_category[cat].sort(key=get_engagement, reverse=True)
        by_category[cat] = by_category[cat][:MAX_ITEMS_PER_CATEGORY]

    # Hot topics: repeated items that are still high-engagement (ongoing major stories)
    repeated_items.sort(key=get_engagement, reverse=True)
    hot_topics = repeated_items[:8]

    # Entertainment: separate stream for entertainment-categorized items
    entertainment_items = by_category.pop("entertainment", [])

    def item_to_dict(item):
        d = {
            "source": item.get("source", ""),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "author": item.get("author", ""),
            "subreddit": item.get("subreddit", ""),
            "likes": item.get("likes", 0),
            "retweets": item.get("retweets", 0),
            "points": item.get("points", 0),
            "score": item.get("score", 0),
            "comments": item.get("comments", 0),
            "created_at": item.get("created_at", ""),
        }
        if item.get("source") == "x":
            first_line = item.get("title", "").split("\n")[0][:120]
            d["first_line"] = first_line
        return d

    # Build output
    output = {
        "date": today,
        "total_raw": {
            "x": source_counts.get("x", 0),
            "hn": source_counts.get("hn", 0),
            "reddit": source_counts.get("reddit", 0),
            "science": source_counts.get("science", 0),
            "china": source_counts.get("china", 0),
            "baidu_hot": source_counts.get("baidu_hot", 0),
        },
        "total_new": len(new_items),
        "total_repeated": len(repeated_items),
        # Today's news by category (no entertainment)
        "today_news": {},
        # Hot topics that persist across days (top repeated)
        "hot_topics": [item_to_dict(it) for it in hot_topics],
        # Entertainment
        "entertainment": [item_to_dict(it) for it in entertainment_items[:10]],
        # Science & China from RSS
        "science": science_items,
        "china": china_items,
        # Baidu Hot Search (国内热点/娱乐)
        "baidu_hot": baidu_hot[:20],
    }

    for cat_name in NEWS_CATEGORY_ORDER:
        output["today_news"][cat_name] = [
            {
                "source": item.get("source", ""),
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "author": item.get("author", ""),
                "subreddit": item.get("subreddit", ""),
                "likes": item.get("likes", 0),
                "retweets": item.get("retweets", 0),
                "points": item.get("points", 0),
                "score": item.get("score", 0),
                "comments": item.get("comments", 0),
                "created_at": item.get("created_at", ""),
                "category": item.get("category", cat_name),
            }
            for item in by_category.get(cat_name, [])
        ]

    # General category for items that didn't match elsewhere
    output["today_news"]["general"] = [
        {
            "source": item.get("source", ""),
            "title": item.get("title", ""),
            "url": item.get("url", ""),
            "author": item.get("author", ""),
            "subreddit": item.get("subreddit", ""),
            "likes": item.get("likes", 0),
            "retweets": item.get("retweets", 0),
            "points": item.get("points", 0),
            "score": item.get("score", 0),
            "comments": item.get("comments", 0),
            "created_at": item.get("created_at", ""),
        }
        for item in by_category.get("general", [])
    ]

    # Print the JSON output
    print(json.dumps(output, ensure_ascii=False, indent=2))

    if os.environ.get("CRON_RUN") == "1":
        save_seen_stories(seen)
        eprint(f"\n✓ Seen stories persisted ({len(new_items)} new, {len(repeated_items)} repeated)")
    else:
        eprint(f"\n⚠ CRON_RUN not set — seen stories NOT persisted")

    eprint(f"\n✓ Done. New: {len(new_items)} | Repeated: {len(repeated_items)}")


if __name__ == "__main__":
    main()
