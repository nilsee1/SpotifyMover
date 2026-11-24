# SpotifyMover 🎵

A clean, modular Python CLI tool that imports playlists into a **Navidrome** music server via the Subsonic API **without requiring any Spotify API keys or developer accounts**, with optional **Lidarr API v1 integration** to automatically resolve and monitor missing albums for unmatched tracks.

---

## ✨ Features

- **Zero Spotify API Keys:**
  - Parse **public Spotify playlists** directly from Spotify's public embed page (`__NEXT_DATA__` JSON).
  - Parse **Exportify CSV files** (`https://exportify.net`) for private playlists or playlists exceeding 100 tracks.
- **Smart 100-Track Cap Detection:** Automatically notifies you when a Spotify embed page caps at 100 tracks and recommends using an Exportify CSV.
- **Robust Normalization & Fuzzy Matching:**
  - Strips diacritics and accents (Unicode `NFKD`).
  - Cleans common audio release noise: `remastered`, `deluxe`, `radio edit`, `single version`, `explicit`, `feat.` / `ft.` clauses.
  - Weighted fuzzy scoring via `rapidfuzz` (60% title, 40% artist) with a +5 duration tie-breaker bonus for tracks matching within 5 seconds.
  - Multi-tier search strategy: searches `"{primary_artist} {clean_title}"` with fallback to `"{clean_title}"`.
- **Subsonic API Security & Reliability:**
  - Authenticates via Subsonic API v1.16.1 token authentication (random hex salt + MD5 hash) — **never transmits plaintext passwords**.
  - Sends playlist track additions as HTTP `POST` form data (`application/x-www-form-urlencoded`), eliminating HTTP 414 (*URI Too Long*) errors on large playlists.
  - Verifies server connectivity via `rest/ping.view` at startup.
- **Lidarr Integration for Missing Tracks (`--send-to-lidarr`):**
  - **MusicBrainz Fallback Resolution:** Resolves album metadata when missing from Spotify embed playlists, adhering to rate limits and prioritizing official releases (Albums, Singles, EPs).
  - **Safe Album Monitoring:** Adds new artists with `monitor: "none"` and `searchForMissingAlbums: False`, monitoring *only* the specific target album(s) without downloading entire artist discographies.
  - **Lidarr Metadata Refresh Quirk Protection:** Explicitly updates album monitoring via `/api/v1/album/monitor` and handles Lidarr's async metadata refresh.
  - **Tagging:** Automatically tags newly added or modified artists with `SpotifyMoverTool` (or custom `LIDARR_TAG`) while preserving all existing artist tags. Handles lowercase validation compatibility.
  - **Selective & Automated Workflows:** Interactive album selection (`--lidarr-select`), minimum tracks per album filter (`--lidarr-min-tracks`), batched `AlbumSearch` command (`--lidarr-search`), and automated unattended runs (`--yes`).
- **Rich Terminal UX:**
  - Live progress bars during track matching, metadata resolution, and Lidarr sync.
  - Summary tables showing matched/unmatched metrics and percentages.
  - Formatted preview table for planned Lidarr album actions.
  - Interactive confirmation or automated overwrite mode (`--update`, `--yes`).
  - Export unmatched tracks to `.txt` or `.csv` files for further inspection.

---

## 📁 Project Structure

```
SpotifyMover/
├── sync_playlist.py           # CLI entry point (argparse, interactive prompt, rich UX)
├── src/
│   ├── models.py              # Dataclasses: Track, MatchResult, PlaylistData, MissingAlbumGroup, LidarrAlbumPlan, LidarrSyncSummary
│   ├── navidrome.py           # Subsonic API client (auth, ping, search, playlist create/update)
│   ├── matcher.py             # Normalization, search fallback strategy, rapidfuzz scoring
│   ├── musicbrainz.py         # Polite MusicBrainz client for album resolution & caching
│   ├── lidarr.py              # Lidarr REST API v1 client, tag management, and safe album monitoring
│   └── sources/
│       ├── spotify_embed.py   # Public Spotify embed scraper (__NEXT_DATA__)
│       └── exportify_csv.py   # Exportify CSV parser (UTF-8-SIG BOM, multi-artist splitting)
├── tests/                     # Unit test suite (pytest)
│   ├── test_normalization.py  # Diacritics, noise tags, remasters, hyphens, feat clauses
│   ├── test_matcher.py        # Scoring edge cases, duration bonus, search fallback
│   ├── test_exportify_csv.py  # CSV parsing, BOM, header variants, playlist naming
│   ├── test_spotify_embed.py  # URL regex extraction, HTML/JSON parsing, 100-track warning
│   ├── test_navidrome.py      # Subsonic token auth, ping, POST form-data verification
│   ├── test_musicbrainz.py    # MusicBrainz lookup, release preference, rate limiting & cache
│   ├── test_lidarr.py         # Config, profile/folder resolution, tag quirk, safe monitoring
│   └── test_cli.py            # CLI helper functions, exports, range parser, dry-run
├── requirements.txt           # Project dependencies
├── .env.example               # Template for Navidrome and Lidarr configuration
├── .gitignore                 # Git ignore rules
├── pytest.ini                 # Pytest configuration
└── README.md                  # Documentation and usage guide
```

---

## 🚀 Installation & Setup

### 1. Prerequisites
- Python 3.10 or higher

### 2. Clone and Setup Environment
```bash
git clone https://github.com/your-username/SpotifyMover.git
cd SpotifyMover

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Credentials
Copy `.env.example` to `.env` and fill in your details:
```bash
cp .env.example .env
```

Edit `.env`:
```env
# Navidrome / Subsonic Server Credentials (Required)
NAVIDROME_URL=https://music.yourdomain.com
NAVIDROME_USER=your_username
NAVIDROME_PASSWORD=your_password

# Lidarr API Configuration (Required only when using --send-to-lidarr)
LIDARR_URL=https://lidarr.yourdomain.com
LIDARR_API_KEY=your_lidarr_api_key

# Optional Lidarr settings (auto-selected if only 1 exists in Lidarr)
LIDARR_ROOT_FOLDER=/music
LIDARR_QUALITY_PROFILE=Standard
LIDARR_METADATA_PROFILE=Standard
LIDARR_TAG=SpotifyMoverTool
```

> [!TIP]
> **Metadata Profile Tip:** In Lidarr, ensure your chosen metadata profile includes **Singles/EPs** if you want single missing tracks from playlists to be monitored.

---

## 📖 Usage

### Basic Usage
Pass a Spotify playlist URL/ID or path to an Exportify CSV:

```bash
# Public Spotify Playlist URL
./sync_playlist.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"

# Raw Spotify Playlist ID
./sync_playlist.py 37i9dQZF1DXcBWIGoYBM5M

# Exportify CSV file
./sync_playlist.py ./path/to/my_playlist.csv
```

If you run `./sync_playlist.py` without arguments, you will be prompted interactively in the terminal:
```bash
./sync_playlist.py
```

---

### Command-Line Options

| Option | Description |
|---|---|
| `source` | *(Optional positional)* Spotify URL/ID or path to an Exportify `.csv` file. |
| `--name <str>` | Custom target playlist name on Navidrome (overrides default/derived name). |
| `--threshold <int>` | Fuzzy match threshold percentage from 0 to 100 (default: `80`). |
| `--update` | Overwrite existing playlist on Navidrome without prompting. |
| `--dry-run` | Match tracks and print preview without making changes to Navidrome or Lidarr. |
| `--export-missing <path>` | Export unmatched tracks to a `.txt` or `.csv` file. |
| `--verbose` | Print match score and detailed metadata for each track and plan. |
| `--send-to-lidarr` | Resolve unmatched tracks to albums and preview/send them to Lidarr. |
| `--lidarr-search` | Immediately trigger an `AlbumSearch` command in Lidarr for all newly monitored albums. |
| `--lidarr-min-tracks <int>` | Only send an album to Lidarr if at least `N` missing tracks belong to it (default: `1`). |
| `--lidarr-select` | Interactively choose which resolved albums to send to Lidarr (`all`, `none`, or e.g. `1,3-5`). |
| `--yes`, `-y` | Skip interactive confirmation prompts (useful for automation/cronjobs). |

---

### Examples

#### 1. Dry Run with Verbose Match Details
Verify which tracks match your Navidrome library before creating the playlist:
```bash
./sync_playlist.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" --dry-run --verbose
```

#### 2. Preview Sending Missing Tracks to Lidarr
Inspect which missing tracks resolve to albums and what Lidarr would do, without touching Lidarr or Navidrome:
```bash
./sync_playlist.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" --dry-run --send-to-lidarr
```

#### 3. Import Playlist and Send Missing Albums to Lidarr with Search
Sync to Navidrome, prompt to send missing albums to Lidarr, and trigger a search for newly monitored albums:
```bash
./sync_playlist.py ./Synthwave.csv --send-to-lidarr --lidarr-search
```

#### 4. Filter Albums by Minimum Missing Tracks
Only send albums to Lidarr if at least 2 tracks from that album are missing (avoids downloading entire albums for one-off tracks):
```bash
./sync_playlist.py ./Synthwave.csv --send-to-lidarr --lidarr-min-tracks 2
```

#### 5. Interactively Select Albums for Lidarr
Choose specific albums to monitor using indices or ranges (e.g. `1,3-5`):
```bash
./sync_playlist.py ./RockClassics.csv --send-to-lidarr --lidarr-select
```

#### 6. Unattended Automation (Cron / Headless)
Automatically update existing playlists and send missing albums to Lidarr without interactive prompts:
```bash
./sync_playlist.py 37i9dQZF1DXcBWIGoYBM5M --update --send-to-lidarr --lidarr-search --yes
```

---

## 🧪 Running Unit Tests

The test suite covers track normalization, fuzzy scoring edge cases, CSV parsing, Spotify embed parsing, Subsonic authentication, MusicBrainz resolution, Lidarr API workflows, and CLI functionality:

```bash
pytest
```

To run with verbose output:
```bash
pytest -v
```

---

## 💡 Notes & Best Practices

- **Playlists with > 100 tracks:** Spotify's public embed page caps results at 100 tracks. For larger playlists or private playlists, use [Exportify](https://exportify.net) to download a `.csv` file with one click and run `sync_playlist.py` on the downloaded file.
- **Fuzzy matching threshold:** The default threshold is `80`. If you have different editions of songs and want looser matching, try `--threshold 75`. For strict matching, try `--threshold 85` or `90`.
- **Lidarr Discography Safety:** SpotifyMover will *never* monitor an artist's full discography. When an artist is newly added to Lidarr, their discography is set to unmonitored (`addOptions.monitor = "none"`), and only the specific album containing the missing tracks is monitored.
- **Lidarr Tagging:** All artists added or modified by SpotifyMover receive the `SpotifyMoverTool` tag (configurable via `LIDARR_TAG`), making it easy to filter or manage them in the Lidarr UI.
