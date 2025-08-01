#!/usr/bin/env python3

from __future__ import annotations
import argparse
import asyncio
import json
import logging
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, TextIO

import aiohttp
from aiohttp import ClientResponseError
from dateutil.parser import isoparse
from tenacity import RetryError, retry, stop_after_attempt, wait_random_exponential

# ───────────────────────── CONFIG ─────────────────────────
REDDIT_SEARCH = "https://www.reddit.com/search.json"
REDDIT_SUBREDDIT = "https://www.reddit.com/r/{subreddit}/new.json"
REDDIT_SUBREDDIT_SEARCH = "https://www.reddit.com/r/{subreddit}/search.json"
REDDIT_COMMENTS = "https://www.reddit.com/comments/{pid}.json"
PAGE_SIZE = 100
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%SZ",
)
log = logging.getLogger("reddit-scraper")

# ──────────────────────── DATA MODELS ─────────────────────
@dataclass
class PostContext:
    post_id: str
    title: str | None
    content: str | None

@dataclass
class Record:
    source: str
    keyword: str
    type: str  # "post" | "comment"
    timestamp: str
    url: str
    title: Optional[str]
    content: str
    post_id: Optional[str] = None
    comment_id: Optional[str] = None
    post_context: Optional[PostContext] = None
    meta: Dict[str, Any] | None = None

    def to_jsonl(self) -> str:
        d = asdict(self)
        if self.post_context:
            d["post_context"] = asdict(self.post_context)
        # drop None values
        return json.dumps({k: v for k, v in d.items() if v is not None}, ensure_ascii=False) + "\n"

# ──────────────────────── TIME HELPERS ────────────────────
_SINCE_RE = re.compile(r"^(\d+)([dmy])$", re.I)

def parse_since(expr: str) -> int:
    m = _SINCE_RE.match(expr)
    if not m:
        raise ValueError("--since must look like 30d / 6m / 2y")
    num, unit = int(m.group(1)), m.group(2).lower()
    if unit == "d":
        delta = timedelta(days=num)
    elif unit == "m":
        delta = timedelta(days=30 * num)
    else:
        delta = timedelta(days=365 * num)
    return int((datetime.now(tz=timezone.utc) - delta).timestamp())

def to_epoch(date_s: str) -> int:
    """YYYY-MM-DD → epoch seconds (UTC)."""
    return int(isoparse(date_s).replace(tzinfo=timezone.utc).timestamp())

def reddit_window(start: int, end: int) -> str:
    """Pick Reddit search 't=' window closest to span."""
    span = (end - start) / 86400
    if span <= 1:
        return "day"
    if span <= 7:
        return "week"
    if span <= 31:
        return "month"
    if span <= 365:
        return "year"
    return "all"

# ──────────────────────── HTTP HELPERS ───────────────────
@retry(
    wait=wait_random_exponential(multiplier=3, max=600),
    stop=stop_after_attempt(12),
)
async def reddit_search_page(
    sess: aiohttp.ClientSession, kw: str, after: Optional[str], t: str
) -> Tuple[List[dict], Optional[str]]:
    """One page of /search.json (newest→oldest)."""
    params = {
        "q": kw,
        "limit": str(PAGE_SIZE),
        "sort": "new",
        "t": t,
        "raw_json": 1,
    }
    if after:
        params["after"] = after

    async with sess.get(REDDIT_SEARCH, params=params, timeout=40) as resp:
        if resp.status == 429:
            raise ClientResponseError(resp.request_info, resp.history, status=429, message="rate limit")
        resp.raise_for_status()
        js = await resp.json()
        kids = js.get("data", {}).get("children", [])
        return [k["data"] for k in kids], js.get("data", {}).get("after")

@retry(
    wait=wait_random_exponential(multiplier=3, max=600),
    stop=stop_after_attempt(12),
)
async def reddit_subreddit_page(
    sess: aiohttp.ClientSession, subreddit: str, after: Optional[str]
) -> Tuple[List[dict], Optional[str]]:
    """One page of /r/{subreddit}/new.json (newest→oldest)."""
    params = {
        "limit": str(PAGE_SIZE),
        "raw_json": 1,
    }
    if after:
        params["after"] = after

    async with sess.get(REDDIT_SUBREDDIT.format(subreddit=subreddit), params=params, timeout=40) as resp:
        if resp.status == 429:
            raise ClientResponseError(resp.request_info, resp.history, status=429, message="rate limit")
        resp.raise_for_status()
        js = await resp.json()
        kids = js.get("data", {}).get("children", [])
        return [k["data"] for k in kids], js.get("data", {}).get("after")

@retry(
    wait=wait_random_exponential(multiplier=3, max=600),
    stop=stop_after_attempt(12),
)
async def reddit_subreddit_search_page(
    sess: aiohttp.ClientSession, subreddit: str, keyword: str, after: Optional[str], t: str
) -> Tuple[List[dict], Optional[str]]:
    """Search for keyword within specific subreddit."""
    params = {
        "q": keyword,
        "limit": str(PAGE_SIZE),
        "sort": "new",
        "t": t,
        "restrict_sr": "true",
        "raw_json": 1,
    }
    if after:
        params["after"] = after

    async with sess.get(REDDIT_SUBREDDIT_SEARCH.format(subreddit=subreddit), params=params, timeout=40) as resp:
        if resp.status == 429:
            raise ClientResponseError(resp.request_info, resp.history, status=429, message="rate limit")
        resp.raise_for_status()
        js = await resp.json()
        kids = js.get("data", {}).get("children", [])
        return [k["data"] for k in kids], js.get("data", {}).get("after")

async def fetch_comments(sess: aiohttp.ClientSession, pid: str) -> List[dict]:
    """Download full comment tree for a submission."""
    async with sess.get(
        REDDIT_COMMENTS.format(pid=pid),
        params={"limit": 500, "raw_json": 1},
        timeout=60,
    ) as r:
        if r.status != 200:
            return []
        thread = await r.json()

    collected: List[dict] = []

    def walk(node):
        if isinstance(node, dict) and node.get("kind") == "t1":
            d = node.get("data", {})
            collected.append(d)
            repl = d.get("replies")
            if isinstance(repl, dict):
                for ch in repl.get("data", {}).get("children", []):
                    walk(ch)

    if len(thread) > 1:
        for top in thread[1].get("data", {}).get("children", []):
            walk(top)

    return collected

# ───────────────────────── BUILDERS ──────────────────────
def _iso(ts: int) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()

def build_post(sub: dict, search_term: str) -> Record:
    return Record(
        source="reddit",
        keyword=search_term,
        type="post",
        post_id=sub["id"],
        timestamp=_iso(int(sub["created_utc"])),
        url=f"https://www.reddit.com{sub.get('permalink','')}",
        title=sub.get("title"),
        content=sub.get("selftext", ""),
        meta={
            "subreddit": sub.get("subreddit"),
            "author": sub.get("author"),
            "score": sub.get("score"),
            "num_comments": sub.get("num_comments"),
        },
    )

def build_comment(cm: dict, search_term: str, pc: PostContext) -> Record:
    parent_raw = cm.get("parent_id", "")
    parent_comment_id = parent_raw.split("_")[-1] if parent_raw.startswith("t1_") else None

    return Record(
        source="reddit",
        keyword=search_term,
        type="comment",
        comment_id=cm.get("id"),
        timestamp=_iso(int(cm["created_utc"])),
        url=f"https://www.reddit.com/comments/{pc.post_id}/_/{cm.get('id')}",
        title=None,
        content=cm.get("body", ""),
        post_context=pc,
        meta={
            "subreddit": cm.get("subreddit"),
            "author": cm.get("author"),
            "score": cm.get("score"),
            "parent_comment_id": parent_comment_id,
        },
    )

# ───────────────────────── COLLECTORS ─────────────────────
async def scrape_keyword(
    kw: str,
    start_ts: int,
    end_ts: int,
    processed_posts: Set[str],
    sleep_s: float,
    post_cap: Optional[int],
    sess: aiohttp.ClientSession,
    file_pointer: TextIO,
):
    """Collect all records for one keyword (global search) - writes directly to file."""
    after = None
    t_val = reddit_window(start_ts, end_ts)
    posts = 0

    log.info("[global:%s] searching globally from %s to %s",
             kw,
             datetime.fromtimestamp(start_ts, tz=timezone.utc).date(),
             datetime.fromtimestamp(end_ts, tz=timezone.utc).date())

    while True:
        try:
            subs, after = await reddit_search_page(sess, kw, after, t_val)
        except (RetryError, ClientResponseError) as e:
            log.warning("[global:%s] halted: %s", kw, e)
            break

        if not subs:
            break

        for sub in subs:
            crt = int(sub["created_utc"])
            if not (start_ts <= crt <= end_ts):
                continue

            post_id = sub["id"]
            if post_id in processed_posts:
                continue
            
            processed_posts.add(post_id)

            # Write post immediately
            post_rec = build_post(sub, f"global:{kw}")
            file_pointer.write(post_rec.to_jsonl())
            file_pointer.flush()
            posts += 1

            # Write comments immediately
            ctx = PostContext(post_id=post_rec.post_id,
                            title=post_rec.title,
                            content=post_rec.content)
            
            for cm in await fetch_comments(sess, post_rec.post_id):
                cm_ts = int(cm["created_utc"])
                if start_ts <= cm_ts <= end_ts:
                    file_pointer.write(build_comment(cm, f"global:{kw}", ctx).to_jsonl())
                    file_pointer.flush()

            if post_cap and posts >= post_cap:
                log.info("[global:%s] reached post cap %d", kw, post_cap)
                return

        log.info("[global:%s] posts processed: %d", kw, posts)
        if not after:
            break
        await asyncio.sleep(sleep_s)

async def scrape_subreddit(
    subreddit: str,
    start_ts: int,
    end_ts: int,
    processed_posts: Set[str],
    sleep_s: float,
    post_cap: Optional[int],
    sess: aiohttp.ClientSession,
    file_pointer: TextIO,
):
    """Collect all records for one subreddit (dump all posts) - writes directly to file."""
    after = None
    posts = 0

    log.info("[subreddit:%s] dumping all posts from %s to %s",
             subreddit,
             datetime.fromtimestamp(start_ts, tz=timezone.utc).date(),
             datetime.fromtimestamp(end_ts, tz=timezone.utc).date())

    while True:
        try:
            subs, after = await reddit_subreddit_page(sess, subreddit, after)
        except (RetryError, ClientResponseError) as e:
            log.warning("[subreddit:%s] halted: %s", subreddit, e)
            break

        if not subs:
            break

        for sub in subs:
            crt = int(sub["created_utc"])
            if not (start_ts <= crt <= end_ts):
                continue

            post_id = sub["id"]
            if post_id in processed_posts:
                continue
            
            processed_posts.add(post_id)

            # Write post immediately
            post_rec = build_post(sub, f"subreddit:{subreddit}")
            file_pointer.write(post_rec.to_jsonl())
            file_pointer.flush()
            posts += 1

            # Write comments immediately
            ctx = PostContext(post_id=post_rec.post_id,
                            title=post_rec.title,
                            content=post_rec.content)
            
            for cm in await fetch_comments(sess, post_rec.post_id):
                cm_ts = int(cm["created_utc"])
                if start_ts <= cm_ts <= end_ts:
                    file_pointer.write(build_comment(cm, f"subreddit:{subreddit}", ctx).to_jsonl())
                    file_pointer.flush()

            if post_cap and posts >= post_cap:
                log.info("[subreddit:%s] reached post cap %d", subreddit, post_cap)
                return

        log.info("[subreddit:%s] posts processed: %d", subreddit, posts)
        if not after:
            break
        await asyncio.sleep(sleep_s)

async def scrape_targeted(
    keyword: str,
    subreddit: str,
    start_ts: int,
    end_ts: int,
    processed_posts: Set[str],
    sleep_s: float,
    post_cap: Optional[int],
    sess: aiohttp.ClientSession,
    file_pointer: TextIO,
):
    """Collect records for keyword within specific subreddit - writes directly to file."""
    after = None
    t_val = reddit_window(start_ts, end_ts)
    posts = 0

    log.info("[targeted] searching '%s' in r/%s from %s to %s",
             keyword, subreddit,
             datetime.fromtimestamp(start_ts, tz=timezone.utc).date(),
             datetime.fromtimestamp(end_ts, tz=timezone.utc).date())

    while True:
        try:
            subs, after = await reddit_subreddit_search_page(sess, subreddit, keyword, after, t_val)
        except (RetryError, ClientResponseError) as e:
            log.warning("[targeted:%s in %s] halted: %s", keyword, subreddit, e)
            break

        if not subs:
            break

        for sub in subs:
            crt = int(sub["created_utc"])
            if not (start_ts <= crt <= end_ts):
                continue

            post_id = sub["id"]
            if post_id in processed_posts:
                continue
            
            processed_posts.add(post_id)

            # Write post immediately
            post_rec = build_post(sub, f"targeted:{keyword}@{subreddit}")
            file_pointer.write(post_rec.to_jsonl())
            file_pointer.flush()
            posts += 1

            # Write comments immediately
            ctx = PostContext(post_id=post_rec.post_id,
                            title=post_rec.title,
                            content=post_rec.content)
            
            for cm in await fetch_comments(sess, post_rec.post_id):
                cm_ts = int(cm["created_utc"])
                if start_ts <= cm_ts <= end_ts:
                    file_pointer.write(build_comment(cm, f"targeted:{keyword}@{subreddit}", ctx).to_jsonl())
                    file_pointer.flush()

            if post_cap and posts >= post_cap:
                log.info("[targeted:%s in %s] reached post cap %d", keyword, subreddit, post_cap)
                return

        log.info("[targeted:%s in %s] posts processed: %d", keyword, subreddit, posts)
        if not after:
            break
        await asyncio.sleep(sleep_s)

# ─────────────────────── ORCHESTRATOR ────────────────────
async def scrape_all(
    kw_list: List[str],
    subreddit_list: List[str],
    start_ts: int,
    end_ts: int,
    out_path: Path,
    sleep_s: float,
    post_cap: Optional[int],
    all_subreddits: bool,
    global_plus_targeted: bool,
):
    """Smart orchestration based on arguments provided."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    total_before = out_path.stat().st_size if out_path.exists() else 0

    processed_posts: Set[str] = set()

    with out_path.open("a", encoding="utf-8") as fp:
        async with aiohttp.ClientSession(headers={"User-Agent": UA}) as sess:
            
            # NEW: Global + Targeted mode
            if global_plus_targeted and kw_list and subreddit_list:
                log.info("Comprehensive mode: %d global keywords + %d targeted keyword-subreddit combinations", 
                         len(kw_list), len(kw_list) * len(subreddit_list))
                
                # Phase 1: Global keyword search
                log.info("Phase 1: Global keyword search")
                for kw in kw_list:
                    if kw.strip():
                        await scrape_keyword(
                            kw=kw.strip(),
                            start_ts=start_ts,
                            end_ts=end_ts,
                            processed_posts=processed_posts,
                            sleep_s=sleep_s,
                            post_cap=post_cap,
                            sess=sess,
                            file_pointer=fp,
                        )
                
                # Phase 2: Targeted search (keywords within subreddits)
                log.info("Phase 2: Targeted keyword-in-subreddit search")
                for kw in kw_list:
                    for subreddit in subreddit_list:
                        if kw.strip() and subreddit.strip():
                            await scrape_targeted(
                                keyword=kw.strip(),
                                subreddit=subreddit.strip(),
                                start_ts=start_ts,
                                end_ts=end_ts,
                                processed_posts=processed_posts,
                                sleep_s=sleep_s,
                                post_cap=post_cap,
                                sess=sess,
                                file_pointer=fp
                            )
            
            # Traditional mode: Global search + subreddit dump
            elif all_subreddits and kw_list and subreddit_list:
                log.info("Traditional mode: %d global keywords + %d subreddit dumps", 
                         len(kw_list), len(subreddit_list))
                
                # Global keyword search
                for kw in kw_list:
                    if kw.strip():
                        await scrape_keyword(
                            kw=kw.strip(),
                            start_ts=start_ts,
                            end_ts=end_ts,
                            processed_posts=processed_posts,
                            sleep_s=sleep_s,
                            post_cap=post_cap,
                            sess=sess,
                            file_pointer=fp,
                        )

                # Subreddit dump (all posts)
                for subreddit in subreddit_list:
                    if subreddit.strip():
                        await scrape_subreddit(
                            subreddit=subreddit.strip(),
                            start_ts=start_ts,
                            end_ts=end_ts,
                            processed_posts=processed_posts,
                            sleep_s=sleep_s,
                            post_cap=post_cap,
                            sess=sess,
                            file_pointer=fp,
                        )
            
            # Smart mode: Targeted search only
            elif kw_list and subreddit_list:
                log.info("Smart mode: searching %d keywords within %d subreddits", 
                         len(kw_list), len(subreddit_list))
                
                for kw in kw_list:
                    for subreddit in subreddit_list:
                        if kw.strip() and subreddit.strip():
                            await scrape_targeted(
                                keyword=kw.strip(),
                                subreddit=subreddit.strip(),
                                start_ts=start_ts,
                                end_ts=end_ts,
                                processed_posts=processed_posts,
                                sleep_s=sleep_s,
                                post_cap=post_cap,
                                sess=sess,
                                file_pointer=fp
                            )
            
            # Keywords only: Global search
            elif kw_list:
                log.info("Keywords only: %d global searches", len(kw_list))
                for kw in kw_list:
                    if kw.strip():
                        await scrape_keyword(
                            kw=kw.strip(),
                            start_ts=start_ts,
                            end_ts=end_ts,
                            processed_posts=processed_posts,
                            sleep_s=sleep_s,
                            post_cap=post_cap,
                            sess=sess,
                            file_pointer=fp,
                        )
            
            # Subreddits only: Subreddit dump
            elif subreddit_list:
                log.info("Subreddits only: %d subreddit dumps", len(subreddit_list))
                for subreddit in subreddit_list:
                    if subreddit.strip():
                        await scrape_subreddit(
                            subreddit=subreddit.strip(),
                            start_ts=start_ts,
                            end_ts=end_ts,
                            processed_posts=processed_posts,
                            sleep_s=sleep_s,
                            post_cap=post_cap,
                            sess=sess,
                            file_pointer=fp,
                        )

    total_after = out_path.stat().st_size
    log.info("Finished. Wrote %s (Δ %.1f KB) | Unique posts: %d",
             out_path, (total_after - total_before) / 1024, len(processed_posts))

# ─────────────────────────── CLI ────────────────────────
def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enhanced Reddit Scraper with Multiple Search Modes")
    p.add_argument("--keywords",
                   help="file with one keyword per line")
    p.add_argument("--subreddits",
                   help="file with one subreddit per line")
    p.add_argument("--all-subreddits", action="store_true",
                   help="force global search + subreddit dump (traditional mode)")
    p.add_argument("--global-plus-targeted", action="store_true",
                   help="global keyword search + targeted keyword-in-subreddit search")
    p.add_argument("--out", default="reddit.ndjson",
                   help="output NDJSON file (append)")
    p.add_argument("--since", default="1y",
                   help="relative window like 30d / 6m / 2y")
    p.add_argument("--from", dest="from_date",
                   help="absolute start date YYYY-MM-DD (UTC)")
    p.add_argument("--to", dest="to_date",
                   help="absolute end date YYYY-MM-DD (UTC)")
    p.add_argument("--sleep", type=float, default=3,
                   help="sleep seconds between Reddit pages")
    p.add_argument("--post-limit", type=int, default=0,
                   help="max posts per search (0 = unlimited)")
    return p.parse_args()

def compute_range(args) -> Tuple[int, int]:
    now_ts = int(datetime.now(tz=timezone.utc).timestamp())
    if args.from_date:
        start_ts = to_epoch(args.from_date)
        end_ts = to_epoch(args.to_date) if args.to_date else now_ts
    else:
        start_ts = parse_since(args.since)
        end_ts = now_ts

    if start_ts >= end_ts:
        raise SystemExit("Start date must be earlier than end date.")
    return start_ts, end_ts

def load_keywords(path: Optional[str]) -> List[str]:
    if not path or not Path(path).exists():
        return []
    return [ln.strip() for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]

def load_subreddits(path: Optional[str]) -> List[str]:
    if not path or not Path(path).exists():
        return []
    return [ln.strip().lstrip('r/') for ln in Path(path).read_text(encoding="utf-8").splitlines() if ln.strip()]

def main() -> None:
    args = parse_args()
    
    # Validate conflicting flags
    if args.all_subreddits and args.global_plus_targeted:
        raise SystemExit("Cannot use both --all-subreddits and --global-plus-targeted flags together.")
    
    start_ts, end_ts = compute_range(args)
    
    kw_list = load_keywords(args.keywords)
    subreddit_list = load_subreddits(args.subreddits)
    
    if not kw_list and not subreddit_list:
        raise SystemExit("Provide --keywords and/or --subreddits arguments.")
    
    post_cap = None if args.post_limit == 0 else args.post_limit

    asyncio.run(
        scrape_all(
            kw_list,
            subreddit_list,
            start_ts,
            end_ts,
            Path(args.out),
            args.sleep,
            post_cap,
            args.all_subreddits,
            args.global_plus_targeted,  # New parameter
        )
    )

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
