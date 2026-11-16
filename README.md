# yt-overcast-feed

Turns a YouTube playlist into an audio-only podcast feed for Overcast.

A launchd agent on a Mac runs `sync.py` every 4 hours (and after wake if a run was missed). Each run:

1. Reads the playlist from `config.json` (public or unlisted, no login).
2. Downloads audio of new videos with yt-dlp and re-encodes it to mono AAC 64 kbps (about 29 MB per hour).
3. Uploads the files to the `audio` release of this repo.
4. Deletes files of videos removed from the playlist.
5. Rebuilds `feed.xml` and uploads it to the same release.

Feed URL: https://github.com/denbl/yt-overcast-feed/releases/download/audio/feed.xml

## Files

- `sync.py` – the whole sync logic (Python 3 standard library only).
- `config.json` – playlist URL, repo, release tag, feed title.
- `com.denbl.yt-overcast.plist` – launchd agent, copy to `~/Library/LaunchAgents/`.
- `cover.svg`, `cover.jpg` – feed artwork.
- `state/` – local state and log, not committed.

## Requirements

`brew install yt-dlp ffmpeg gh`, and `gh auth login`.

## Commands

```bash
python3 sync.py --dry-run   # build files into ./out, do not touch GitHub
python3 sync.py --rebuild   # re-upload cover.jpg and feed.xml
launchctl kickstart gui/$(id -u)/com.denbl.yt-overcast   # run the agent now
```

If downloads start failing, run `brew upgrade yt-dlp` first and check `state/sync.log`.
