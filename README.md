# SpotifyMover 🎵

A clean, modular Python CLI tool that imports playlists into a **Navidrome** music server via the Subsonic API **without requiring any Spotify API keys or developer accounts**.

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
- **Rich Terminal UX:**
  - Live progress bars during track matching.
  - Summary tables showing matched/unmatched metrics and percentages.
  - Interactive confirmation or automated overwrite mode (`--update`).
  - Export unmatched tracks to `.txt` or `.csv` files for further inspection.

---

## 📁 Project Structure

```
SpotifyMover/
├── sync_playlist.py           # CLI entry point (argparse, interactive prompt, rich UX)
├── src/
│   ├── models.py              # Dataclasses: Track, MatchResult, PlaylistData
│   ├── navidrome.py           # Subsonic API client (auth, ping, search, playlist create/update)
│   ├── matcher.py             # Normalization, search fallback strategy, rapidfuzz scoring
│   └── sources/
│       ├── spotify_embed.py   # Public Spotify embed scraper (__NEXT_DATA__)
│       └── exportify_csv.py   # Exportify CSV parser (UTF-8-SIG BOM, multi-artist splitting)
├── tests/                     # Unit test suite (pytest)
│   ├── test_normalization.py  # Diacritics, noise tags, remasters, hyphens, feat clauses
│   ├── test_matcher.py        # Scoring edge cases, duration bonus, search fallback
│   ├── test_exportify_csv.py  # CSV parsing, BOM, header variants, playlist naming
│   ├── test_spotify_embed.py  # URL regex extraction, HTML/JSON parsing, 100-track warning
│   ├── test_navidrome.py      # Subsonic token auth, ping, POST form-data verification
│   └── test_cli.py            # CLI helper functions and exports
├── requirements.txt           # Project dependencies
├── .env.example               # Template for Navidrome configuration
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
Copy `.env.example` to `.env` and fill in your Navidrome server details:
```bash
cp .env.example .env
```

Edit `.env`:
```env
NAVIDROME_URL=https://music.yourdomain.com
NAVIDROME_USER=your_username
NAVIDROME_PASSWORD=your_password
```

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
| `--dry-run` | Match tracks and print full summary without making changes to Navidrome. |
| `--export-missing <path>` | Export unmatched tracks to a `.txt` or `.csv` file. |
| `--verbose` | Print match score and matched Navidrome metadata for each track. |

---

### Examples

#### 1. Dry Run with Verbose Match Details
Verify which tracks match your Navidrome library before creating the playlist:
```bash
./sync_playlist.py "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M" --dry-run --verbose
```

#### 2. Sync Exportify CSV with Custom Name and Overwrite Existing Playlist
```bash
./sync_playlist.py ./Synthwave.csv --name "Retro Synthwave" --update
```

#### 3. Strict Matching Threshold & Export Missing Tracks
Only accept high-confidence matches (>= 85%) and export missing tracks to a `.csv` file:
```bash
./sync_playlist.py ./Favorites.csv --threshold 85 --export-missing missing_tracks.csv
```

Or export missing tracks as a simple text file (`Artist - Title`):
```bash
./sync_playlist.py ./Favorites.csv --export-missing missing_tracks.txt
```

---

## 🧪 Running Unit Tests

The test suite covers track normalization, fuzzy scoring edge cases, CSV parsing with UTF-8 BOM, Spotify embed parsing, Subsonic authentication, and CLI helpers:

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
