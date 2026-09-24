#!/usr/bin/env python3
"""
Adopt Me! watcher: posts new news-page articles and new YouTube videos
to a Discord webhook.

Setup:
  pip install requests
  export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
  python adoptme_watcher.py

Run it every 5-15 minutes (cron, Task Scheduler, GitHub Actions, etc.).
The first run only records what already exists so you don't get flooded;
pass --post-existing to post the current items instead.
"""
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser

import requests

WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "")
NEWS_URL = "https://www.playadopt.me/news"
SITE = "https://www.playadopt.me"
YOUTUBE_HANDLE_URL = "https://www.youtube.com/@playadoptme"
STATE_FILE = os.environ.get("STATE_FILE", "state.json")
EMBED_COLOR = 0xE81F76
UA = {"User-Agent": "Mozilla/5.0 (compatible; AdoptMeWatcher/1.0)"}


# ---------- helpers ----------
def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get(url):
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    return r.text


def send(payload):
    while True:
        r = requests.post(WEBHOOK_URL, json=payload, timeout=20)
        if r.status_code == 429:
            time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
            continue
        r.raise_for_status()
        return


class TextExtractor(HTMLParser):
    """Very simple HTML -> text for the <main> area of a post."""

    def __init__(self):
        super().__init__()
        self.in_main = False
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == "main":
            self.in_main = True
        if tag in ("script", "style", "nav", "footer", "svg"):
            self.skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "h4"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "nav", "footer", "svg"):
            self.skip = max(0, self.skip - 1)
        if tag == "main":
            self.in_main = False

    def handle_data(self, data):
        if self.in_main and not self.skip and data.strip():
            self.parts.append(data.strip() + " ")


def meta(page, prop):
    m = re.search(
        r'<meta[^>]+(?:property|name)=["\']%s["\'][^>]+content=["\']([^"\']*)["\']' % re.escape(prop),
        page,
    )
    return html.unescape(m.group(1)) if m else ""


# ---------- news ----------
def news_slugs():
    page = get(NEWS_URL)
    slugs = []
    for m in re.finditer(r'href=["\'](?:https://www\.playadopt\.me)?/news/([a-z0-9][a-z0-9\-]*)["\']', page):
        if m.group(1) not in slugs:
            slugs.append(m.group(1))
    return slugs  # page order = newest first


def post_news(slug):
    url = f"{SITE}/news/{slug}"
    page = get(url)
    title = meta(page, "og:title") or slug.replace("-", " ").title()
    image = meta(page, "og:image")
    desc = meta(page, "og:description")

    parser = TextExtractor()
    parser.feed(page)
    body = re.sub(r"\n\s*\n+", "\n\n", "".join(parser.parts)).strip()
    text = body if len(body) > len(desc) else desc
    if len(text) > 1800:
        text = text[:1800].rsplit(" ", 1)[0] + "…\n\n**[Read the full post]({})**".format(url)

    embed = {"title": title[:256], "url": url, "description": text, "color": EMBED_COLOR}
    if image and "Background-News-Paper" not in image:
        embed["image"] = {"url": image}
    send({"embeds": [embed]})


# ---------- youtube ----------
def youtube_channel_id():
    page = get(YOUTUBE_HANDLE_URL)
    m = re.search(r'"(?:channelId|externalId)":"(UC[\w-]{22})"', page) or re.search(
        r'channel_id=(UC[\w-]{22})', page
    )
    if not m:
        raise RuntimeError("Could not find the YouTube channel ID; set YOUTUBE_CHANNEL_ID manually.")
    return m.group(1)


def youtube_videos(channel_id):
    xml = get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    ns = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
    out = []
    for e in ET.fromstring(xml).findall("a:entry", ns):
        out.append((e.find("yt:videoId", ns).text, e.find("a:title", ns).text))
    return out  # newest first


def post_video(vid, title):
    # Discord auto-embeds a playable video preview from the link
    send({"content": f"**{title}**\nhttps://www.youtube.com/watch?v={vid}"})


# ---------- main ----------
def main():
    if not WEBHOOK_URL:
        sys.exit("Set DISCORD_WEBHOOK_URL first.")
    post_existing = "--post-existing" in sys.argv

    state = load_state()
    first_run = state is None
    if first_run:
        state = {"news": [], "videos": [], "channel_id": os.environ.get("YOUTUBE_CHANNEL_ID", "")}

    # News
    try:
        slugs = news_slugs()
        new = [s for s in slugs if s not in state["news"]]
        if first_run and not post_existing:
            new = []
        for s in reversed(new):  # oldest first
            post_news(s)
        state["news"] = list(dict.fromkeys(slugs + state["news"]))[:200]
    except Exception as e:
        print("News check failed:", e)

    # YouTube
    try:
        if not state["channel_id"]:
            state["channel_id"] = youtube_channel_id()
        vids = youtube_videos(state["channel_id"])
        seen = set(state["videos"])
        new = [v for v in vids if v[0] not in seen]
        if first_run and not post_existing:
            new = []
        for vid, title in reversed(new):
            post_video(vid, title)
        state["videos"] = list(dict.fromkeys([v[0] for v in vids] + state["videos"]))[:200]
    except Exception as e:
        print("YouTube check failed:", e)

    save_state(state)


if __name__ == "__main__":
    main()
