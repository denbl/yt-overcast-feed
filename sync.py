#!/usr/bin/python3
"""Mirror a YouTube playlist into an audio-only podcast feed for Overcast.

Every run:
  1. Read the playlist (it must be "unlisted" or public, so no login is needed).
  2. For each new video: download the audio, re-encode to mono AAC 64 kbps,
     upload it as an asset of a GitHub release.
  3. For each video removed from the playlist: delete its asset.
  4. Rebuild feed.xml and upload it to the same release.

Settings live in config.json next to this file. State lives in state/episodes.json.
Run with --dry-run to build files locally without touching GitHub.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from email.utils import formatdate
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text())
STATE_DIR = HERE / "state"
STATE_FILE = STATE_DIR / "episodes.json"
LOCAL_DIR = HERE / "out"  # used only with --dry-run

PLAYLIST = CONFIG["playlist_url"]
REPO = CONFIG["github_repo"]  # "owner/name"
TAG = CONFIG.get("release_tag", "audio")
FEED_TITLE = CONFIG.get("feed_title", "YouTube на пробежку")
COVER = "cover.jpg"  # uploaded once with: python3 sync.py --rebuild

DRY_RUN = "--dry-run" in sys.argv
REBUILD = "--rebuild" in sys.argv  # re-upload cover.jpg and feed.xml even if nothing changed
os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")


def log(msg):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), msg, flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, text=True, capture_output=True, **kw).stdout


def notify(msg):
    subprocess.run(["osascript", "-e", f'display notification "{msg}" with title "YouTube → Overcast"'])


def asset_url(name):
    return f"https://github.com/{REPO}/releases/download/{TAG}/{name}"


def playlist_ids():
    out = run(["yt-dlp", "--flat-playlist", "-J", PLAYLIST])
    return [e["id"] for e in json.loads(out)["entries"] if e and e.get("id")]


def fetch_episode(vid, workdir):
    url = f"https://www.youtube.com/watch?v={vid}"
    info = json.loads(run(["yt-dlp", "-J", "--no-playlist", url]))
    raw = Path(workdir) / f"{vid}.raw.m4a"
    final = Path(workdir) / f"{vid}.m4a"
    run(["yt-dlp", "--no-playlist", "-f", "bestaudio[ext=m4a]/bestaudio", "-o", str(raw), url])
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
         "-map", "0:a", "-map_metadata", "0",
         "-metadata", f"title={info.get('title', vid)}",
         "-metadata", f"artist={info.get('uploader', '')}",
         "-c:a", "aac", "-ac", "1", "-b:a", "64k", "-f", "mp4", str(final)])
    episode = {
        "id": vid,
        "title": info.get("title", vid),
        "channel": info.get("uploader", ""),
        "description": (info.get("description") or "")[:4000],
        "duration": int(info.get("duration") or 0),
        "image": info.get("thumbnail", ""),
        "link": url,
        "added": time.time(),
        "file": final.name,
        "size": final.stat().st_size,
    }
    return episode, final


def upload(path):
    if DRY_RUN:
        LOCAL_DIR.mkdir(exist_ok=True)
        Path(path).replace(LOCAL_DIR / Path(path).name)
        return
    run(["gh", "release", "upload", TAG, str(path), "--clobber", "--repo", REPO])


def upload_copy(path):
    """Upload a file that must stay in place (upload() moves files in --dry-run)."""
    tmp = Path(tempfile.gettempdir()) / Path(path).name
    tmp.write_bytes(Path(path).read_bytes())
    upload(tmp)


def delete_asset(name):
    if DRY_RUN:
        (LOCAL_DIR / name).unlink(missing_ok=True)
        return
    subprocess.run(["gh", "release", "delete-asset", TAG, name, "--yes", "--repo", REPO],
                   capture_output=True, text=True)


def build_feed(episodes):
    items = []
    for ep in sorted(episodes, key=lambda e: e["added"], reverse=True):
        h, m, s = ep["duration"] // 3600, ep["duration"] % 3600 // 60, ep["duration"] % 60
        items.append(f"""    <item>
      <title>{escape(ep['title'])}</title>
      <itunes:author>{escape(ep['channel'])}</itunes:author>
      <description>{escape(ep['channel'] + chr(10) + ep['link'] + chr(10) + chr(10) + ep['description'])}</description>
      <link>{escape(ep['link'])}</link>
      <guid isPermaLink="false">yt-{ep['id']}</guid>
      <pubDate>{formatdate(ep['added'])}</pubDate>
      <enclosure url="{escape(asset_url(ep['file']))}" length="{ep['size']}" type="audio/mp4"/>
      <itunes:duration>{h:02d}:{m:02d}:{s:02d}</itunes:duration>
      <itunes:image href="{escape(ep['image'])}"/>
    </item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
  <channel>
    <title>{escape(FEED_TITLE)}</title>
    <link>{escape(PLAYLIST)}</link>
    <description>Audio from a personal YouTube playlist.</description>
    <language>ru</language>
    <itunes:block>Yes</itunes:block>
    <itunes:image href="{escape(asset_url(COVER))}"/>
    <image><url>{escape(asset_url(COVER))}</url><title>{escape(FEED_TITLE)}</title><link>{escape(PLAYLIST)}</link></image>
{chr(10).join(items)}
  </channel>
</rss>
"""


def main():
    STATE_DIR.mkdir(exist_ok=True)
    episodes = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else []
    known = {e["id"] for e in episodes}

    wanted = playlist_ids()
    log(f"playlist: {len(wanted)} videos, feed: {len(episodes)} episodes")
    changed = False

    for ep in [e for e in episodes if e["id"] not in wanted]:
        log(f"removed from playlist: {ep['title']}")
        delete_asset(ep["file"])
        episodes.remove(ep)
        changed = True

    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        for vid in [v for v in wanted if v not in known]:
            try:
                ep, path = fetch_episode(vid, tmp)
                upload(path)
                episodes.append(ep)
                STATE_FILE.write_text(json.dumps(episodes, ensure_ascii=False, indent=1))
                log(f"added: {ep['title']} ({ep['size'] // 1_000_000} MB)")
                changed = True
            except subprocess.CalledProcessError as e:
                log(f"FAILED {vid}: {(e.stderr or '').strip()[-500:]}")
                failures.append(vid)

    if REBUILD:
        upload_copy(HERE / COVER)
    if changed or REBUILD:
        STATE_FILE.write_text(json.dumps(episodes, ensure_ascii=False, indent=1))
        feed = Path(tempfile.gettempdir()) / "feed.xml"
        feed.write_text(build_feed(episodes))
        upload(feed)
        log("feed updated")

    if failures:
        notify(f"Не удалось скачать {len(failures)} видео. Лог: yt-overcast/state/sync.log")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        log(f"FAILED: {' '.join(e.cmd[:3])}: {(e.stderr or '').strip()[-500:]}")
        notify("Синхронизация плейлиста упала. Лог: yt-overcast/state/sync.log")
        sys.exit(1)
