#!/usr/bin/env python3
"""Scan Craigslist gig sections for mover jobs and push new ones to your phone via Pushover."""
import json
import html
import logging
import re
import sqlite3
import sys
import time
import urllib.error
from datetime import datetime
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
ENV_PATH = BASE / ".env"
DB_PATH = BASE / "seen.db"
USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

CITY = "atlanta"

log = logging.getLogger("cl-movers")


def load_env(path):
    env = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip("'\"")
    return env


class ResultParser(HTMLParser):
    """Parses Craigslist's static (no-JS) search results markup."""

    def __init__(self):
        super().__init__()
        self.posts = []
        self.post = None
        self.fields = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        cls = a.get("class", "") or ""
        if tag == "li" and "cl-static-search-result" in cls:
            self.post = {"title": a.get("title", ""), "url": "", "price": "", "location": ""}
        elif self.post is not None:
            if tag == "a" and a.get("href"):
                self.post["url"] = a["href"]
            elif tag == "div" and cls in ("title", "price", "location"):
                self.fields = cls
                self.post[cls] = ""

    def handle_data(self, data):
        if self.post is not None and self.fields:
            self.post[self.fields] += data.strip()

    def handle_endtag(self, tag):
        if tag == "div":
            self.fields = None
        elif tag == "li" and self.post is not None:
            if self.post["url"]:
                self.post["title"] = html.unescape(self.post["title"])
                self.posts.append(self.post)
            self.post = None


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def post_id(url):
    m = re.search(r"/(\d+)\.html", url)
    return m.group(1) if m else url


def search(city, section, query):
    qs = urllib.parse.urlencode({"query": query, "sort": "date", "postedToday": 1})
    url = f"https://{city}.craigslist.org/search/{section}?{qs}"
    parser = ResultParser()
    parser.feed(fetch(url))
    return parser.posts


def posted_time(url):
    try:
        postedDateTime = re.search(r'<time[^>]*datetime="([^"]+)"', fetch(url))
        if postedDateTime:
            return datetime.strptime(postedDateTime.group(1), "%Y-%m-%dT%H:%M:%S%z")
    except Exception as e:
        log.warning("could not get post time for %s: %s", url, e)
    return None


def notify(craigslistConfig, post):
    posted = posted_time(post["url"])
    posted_text = posted.strftime("%b %d, %I:%M %p").replace(" 0", " ") if posted else ""
    body = " | ".join(x for x in (post["price"], post["location"], posted_text and f"Posted {posted_text}") if x) or "New gig"
    fields = {
        "token": craigslistConfig["pushover_app_token"],
        "user": craigslistConfig["pushover_user_key"],
        "title": post["title"] or "New mover gig",
        "message": body,
        "url": post["url"],
        "url_title": "Open on Craigslist",
        "priority": 1,
    }
    if posted:
        fields["timestamp"] = int(posted.timestamp())
    data = urllib.parse.urlencode(fields).encode()
    endmsg = "\n" + "=" * 40 + "\n"
    print("Found New Job:", post["title"], f"(Posted {posted_text})" if posted_text else "", end=endmsg)
    try:
        urllib.request.urlopen("https://api.pushover.net/1/messages.json", data=data, timeout=30).close()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')}") from None


def scan(craigslistConfig, db):
    first_run = db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0
    exclude = [w.lower() for w in craigslistConfig.get("exclude_words", [])]

    found = {}
    for section in craigslistConfig["sections"]:
        for query in craigslistConfig["queries"]:
            try:
                for p in search(CITY, section, query):
                    found[post_id(p["url"])] = p
            except Exception as e:
                log.warning("fetch failed (%s / %s): %s", section, query, e)
            time.sleep(craigslistConfig.get("request_delay_seconds", 3))

    new = 0
    for pid, p in found.items():
        if db.execute("SELECT 1 FROM seen WHERE id=?", (pid,)).fetchone():
            continue
        if not any(w in p["title"].lower() for w in exclude):
            try:
                if not first_run or craigslistConfig.get("notify_on_first_run"):
                    notify(craigslistConfig, p)
                    log.info("notified: %s", p["title"])
                    new += 1
            except Exception as e:
                log.warning("notify failed for %s: %s (will retry next run)", pid, e)
                continue
        db.execute("INSERT INTO seen VALUES (?,?,?)", (pid, p["title"], p["url"]))
    db.commit()
    log.info("done: %d found, %d notified%s", len(found), new, " (first run, seeded only)" if first_run else "")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = load_env(ENV_PATH)
    craigslistConfig = json.loads(CONFIG_PATH.read_text())
    craigslistConfig["pushover_user_key"] = env.get("PUSHOVER_USER_KEY", "")
    craigslistConfig["pushover_app_token"] = env.get("PUSHOVER_APP_TOKEN", "")
    if not craigslistConfig["pushover_user_key"] or not craigslistConfig["pushover_app_token"] \
            or "CHANGE_ME" in (craigslistConfig["pushover_user_key"], craigslistConfig["pushover_app_token"]):
        sys.exit("Edit .env: set PUSHOVER_USER_KEY and PUSHOVER_APP_TOKEN first.")

    db = sqlite3.connect(DB_PATH)
    db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, title TEXT, url TEXT)")
    interval = craigslistConfig.get("poll_interval_minutes", 10) * 60
    log.info("started, checking every %d minutes (Ctrl+C to stop)", interval // 60)
    try:
        while True:
            try:
                scan(craigslistConfig, db)
            except Exception as e:
                log.error("scan failed: %s", e)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
