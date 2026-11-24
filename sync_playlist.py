#!/usr/bin/env python3
"""CLI tool to import Spotify or Exportify playlists into Navidrome without Spotify API keys."""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Auto-reexec using local .venv if executed with an external or system python interpreter
_script_dir = Path(__file__).resolve().parent
_local_venv = _script_dir / ".venv"
_venv_python = _local_venv / "bin" / "python"
if _venv_python.is_file() and os.access(_venv_python, os.X_OK):
    if Path(sys.prefix).resolve() != _local_venv.resolve():
        os.execv(str(_venv_python), [str(_venv_python)] + sys.argv)

import argparse
import csv
from typing import Any

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
    from rich.prompt import Confirm, Prompt
    from rich.table import Table

    from src.lidarr import (
        LidarrClient,
        LidarrConfig,
        LidarrError,
        apply_lidarr_plans,
        build_lidarr_plans,
        load_lidarr_config,
    )
    from src.matcher import match_playlist_tracks
    from src.models import (
        LidarrAlbumPlan,
        LidarrSyncSummary,
        MatchResult,
        PlaylistData,
        Track,
    )
    from src.navidrome import NavidromeClient, SubsonicError, load_navidrome_credentials
    from src.sources.exportify_csv import parse_exportify_csv
    from src.sources.spotify_embed import fetch_spotify_embed_playlist
except ImportError as _import_err:
    print(
        f"Error: Missing required dependency ({_import_err}).\n"
        "Please install the dependencies in your environment or activate the project virtualenv:\n"
        "  source .venv/bin/activate\n"
        "  pip install -r requirements.txt\n",
        file=sys.stderr,
    )
    sys.exit(1)

console = Console()


def detect_and_load_source(source_input: str, custom_name: str | None = None) -> PlaylistData:
    """Detect whether source is a local CSV file or Spotify URL/ID, and parse it."""
    trimmed = source_input.strip()
    path = Path(trimmed)

    # Check if local file or explicitly ends in .csv
    if path.is_file() or trimmed.lower().endswith(".csv"):
        console.print(f"[cyan]Detected Exportify CSV source:[/] {path}")
        return parse_exportify_csv(path, default_name=custom_name)

    # Otherwise treat as Spotify URL or ID
    console.print(f"[cyan]Detected Spotify Playlist source:[/] {trimmed}")
    with console.status("[bold green]Fetching playlist from Spotify embed page...", spinner="dots"):
        playlist_data = fetch_spotify_embed_playlist(trimmed)

    if custom_name:
        playlist_data.name = custom_name

    return playlist_data


def export_unmatched_tracks(unmatched_results: list[MatchResult], export_path: str | Path) -> None:
    """Export unmatched tracks to .csv or .txt file based on extension."""
    target_path = Path(export_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)

    is_csv = target_path.suffix.lower() == ".csv"

    if is_csv:
        with open(target_path, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["Artist", "Title", "Album", "Duration (s)"])
            for res in unmatched_results:
                t = res.track
                dur_s = round(t.duration_ms / 1000.0, 1) if t.duration_ms else ""
                writer.writerow([t.artist_display, t.title, t.album or "", dur_s])
    else:
        with open(target_path, "w", encoding="utf-8") as f:
            for res in unmatched_results:
                t = res.track
                f.write(f"{t.artist_display} - {t.title}\n")

    console.print(f"[green]✔ Exported {len(unmatched_results)} unmatched tracks to:[/] {target_path}")


def parse_threshold(value_str: str) -> int:
    """Validate that threshold is an integer between 0 and 100."""
    try:
        val = int(value_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Threshold must be an integer, got '{value_str}'")
    if not (0 <= val <= 100):
        raise argparse.ArgumentTypeError(f"Threshold must be between 0 and 100, got {val}")
    return val


def parse_workers(value_str: str) -> int:
    """Validate that workers is an integer >= 1."""
    try:
        val = int(value_str)
    except ValueError:
        raise argparse.ArgumentTypeError(f"Workers must be an integer, got '{value_str}'")
    if val < 1:
        raise argparse.ArgumentTypeError(f"Workers must be at least 1, got {val}")
    return val


def display_results_summary(
    playlist_name: str,
    results: list[MatchResult],
    verbose: bool = False,
) -> tuple[list[MatchResult], list[MatchResult]]:
    """Display matching summary table and missing tracks list."""
    matched = [r for r in results if r.matched]
    unmatched = [r for r in results if not r.matched]
    total = len(results)
    match_pct = (len(matched) / total * 100) if total > 0 else 0.0

    # Summary table
    summary_table = Table(title=f"Sync Summary for '{playlist_name}'", show_header=True, header_style="bold magenta")
    summary_table.add_column("Metric", style="cyan")
    summary_table.add_column("Count", justify="right")
    summary_table.add_column("Percentage", justify="right")

    summary_table.add_row("Total Input Tracks", str(total), "100.0%")
    summary_table.add_row("Matched on Navidrome", f"[green]{len(matched)}[/green]", f"[green]{match_pct:.1f}%[/green]")
    summary_table.add_row("Missing / Unmatched", f"[yellow]{len(unmatched)}[/yellow]", f"[yellow]{100.0 - match_pct:.1f}%[/yellow]")

    console.print()
    console.print(summary_table)

    # Missing tracks table
    if unmatched:
        console.print()
        missing_table = Table(
            title="Unmatched Tracks (Not Found on Navidrome)",
            show_header=True,
            header_style="bold yellow",
        )
        missing_table.add_column("#", justify="right", style="dim", width=4)
        missing_table.add_column("Artist", style="yellow")
        missing_table.add_column("Title", style="white")
        missing_table.add_column("Best Candidate Score", justify="right", style="dim")
        if verbose:
            missing_table.add_column("Best Candidate", style="dim")

        for idx, res in enumerate(unmatched, start=1):
            score_str = f"{res.score:.1f}%" if res.score > 0 else "N/A"
            if verbose:
                cand_str = (
                    f"{res.matched_song.get('artist', 'Unknown')} - {res.matched_song.get('title', 'Unknown')}"
                    if res.matched_song
                    else "None"
                )
                missing_table.add_row(str(idx), res.track.artist_display, res.track.title, score_str, cand_str)
            else:
                missing_table.add_row(str(idx), res.track.artist_display, res.track.title, score_str)

        console.print(missing_table)

    return matched, unmatched


def positive_int(val: str) -> int:
    """Type validator for positive integers (>= 1)."""
    try:
        ival = int(val)
        if ival < 1:
            raise argparse.ArgumentTypeError(f"Value must be a positive integer >= 1 (got {val})")
        return ival
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid positive integer: {val}")


def parse_selection_indices(input_str: str, max_index: int) -> list[int]:
    """Parse selection string like 'all', 'none', or '1,3-5' into 1-based indices."""
    cleaned = input_str.strip().lower()
    if not cleaned or cleaned == "all":
        return list(range(1, max_index + 1))
    if cleaned == "none":
        return []

    indices: set[int] = set()
    parts = cleaned.split(",")
    for part in parts:
        p = part.strip()
        if not p:
            continue
        if "-" in p:
            sub = p.split("-")
            if len(sub) != 2:
                raise ValueError(f"Invalid range syntax: '{p}'")
            try:
                start, end = int(sub[0].strip()), int(sub[1].strip())
            except ValueError:
                raise ValueError(f"Non-integer value in range: '{p}'")
            if start > end:
                raise ValueError(f"Range start ({start}) cannot be greater than end ({end})")
            if start < 1 or end > max_index:
                raise ValueError(f"Range '{p}' out of bounds (1-{max_index})")
            indices.update(range(start, end + 1))
        else:
            try:
                val = int(p)
            except ValueError:
                raise ValueError(f"Invalid integer index: '{p}'")
            if val < 1 or val > max_index:
                raise ValueError(f"Index {val} out of bounds (1-{max_index})")
            indices.add(val)

    return sorted(indices)


def display_lidarr_preview_table(plans: list[LidarrAlbumPlan], verbose: bool = False) -> None:
    """Render a clean Rich preview table of planned Lidarr actions."""
    table = Table(title="Lidarr Missing Albums Plan", show_header=True, header_style="bold magenta")
    table.add_column("#", justify="right", style="dim", width=4)
    table.add_column("Artist", style="yellow")
    table.add_column("Album", style="white")
    table.add_column("Missing Tracks", justify="right")
    table.add_column("Status")

    status_labels = {
        "new_artist_and_album": "[bold green]New Artist + Album[/bold green]",
        "existing_artist_unmonitored": "[bold cyan]Existing Artist (Monitor Album)[/bold cyan]",
        "already_monitored": "[dim]Already Monitored[/dim]",
        "skipped_min_tracks": "[yellow]Skipped (< min tracks)[/yellow]",
        "not_found": "[red]Not Found[/red]",
    }

    for idx, plan in enumerate(plans, start=1):
        artist_name = plan.resolved_artist_name or plan.group.artist
        album_title = plan.resolved_album_title or plan.group.album or "Unknown Album"
        status_styled = status_labels.get(plan.status, plan.status)

        if verbose and plan.group.tracks:
            titles = ", ".join(f"'{t.title}'" for t in plan.group.tracks)
            missing_tracks_str = f"{len(plan.group.tracks)} ({titles})"
        else:
            missing_tracks_str = str(len(plan.group.tracks))

        table.add_row(str(idx), artist_name, album_title, missing_tracks_str, status_styled)

    console.print()
    console.print(table)


def handle_lidarr_integration(unmatched: list[MatchResult], args: argparse.Namespace) -> int:
    """Execute Lidarr album resolution, preview, and monitoring workflow."""
    if not unmatched:
        console.print("[green]All tracks were matched! No missing tracks to send to Lidarr.[/green]")
        return 0

    try:
        lidarr_config = load_lidarr_config()
    except ValueError as exc:
        console.print(f"[bold red]Lidarr Configuration Error:[/bold red] {exc}")
        return 1

    lidarr_client = LidarrClient(url=lidarr_config.url, api_key=lidarr_config.api_key)

    console.print(f"\n[dim]Connecting to Lidarr at {lidarr_config.url}...[/dim]")
    try:
        lidarr_client.test_connection()
        console.print("[green]✔ Lidarr connection verified.[/green]")
    except Exception as exc:
        console.print(f"[bold red]Failed to connect to Lidarr:[/] {exc}")
        return 1

    try:
        resolved_root_folder = lidarr_client.resolve_root_folder(lidarr_config.root_folder)
        resolved_quality_profile = lidarr_client.resolve_quality_profile(lidarr_config.quality_profile)
        resolved_metadata_profile = lidarr_client.resolve_metadata_profile(lidarr_config.metadata_profile)
    except ValueError as exc:
        console.print(f"[bold red]Lidarr Profile/Folder Resolution Error:[/bold red] {exc}")
        return 1

    missing_tracks = [r.track for r in unmatched]
    plans: list[LidarrAlbumPlan] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task("Resolving missing albums for Lidarr...", total=len(missing_tracks))

        def on_lidarr_progress(completed: int, total: int, desc: str) -> None:
            progress.update(task_id, completed=completed, description=f"[cyan]{desc}[/cyan]")

        plans = build_lidarr_plans(
            client=lidarr_client,
            missing_tracks=missing_tracks,
            min_tracks=args.lidarr_min_tracks,
            progress_callback=on_lidarr_progress,
        )

    display_lidarr_preview_table(plans, verbose=args.verbose)

    actionable_plans = [
        p for p in plans if p.status in ("new_artist_and_album", "existing_artist_unmonitored")
    ]

    if not actionable_plans:
        console.print("\n[yellow]No actionable albums to send to Lidarr.[/yellow]")
        return 0

    if args.dry_run:
        console.print(
            f"\n[bold cyan]ℹ Dry run: {len(actionable_plans)} album(s) would be sent to Lidarr "
            f"(tag: '{lidarr_config.tag}'). No changes made.[/bold cyan]"
        )
        return 0

    plans_to_apply = actionable_plans
    if args.lidarr_select and not args.yes:
        console.print(
            f"\n[cyan]Enter indices or ranges of albums to send (1-{len(plans)} from table above, 'all', or 'none'):[/cyan]"
        )
        try:
            choice = Prompt.ask("Selection", default="all")
            selected_indices = set(parse_selection_indices(choice, len(plans)))
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operation aborted by user.[/yellow]")
            return 0
        except ValueError as exc:
            console.print(f"[bold red]Selection Error:[/bold red] {exc}")
            return 1

        plans_to_apply = [
            p for i, p in enumerate(plans, start=1)
            if i in selected_indices and p in actionable_plans
        ]

        if not plans_to_apply:
            console.print("[yellow]No albums selected. Lidarr sync skipped.[/yellow]")
            return 0

    if not args.yes:
        search_desc = "yes" if args.lidarr_search else "no"
        prompt_msg = f"Send {len(plans_to_apply)} album(s) to Lidarr (tag: '{lidarr_config.tag}', trigger search: {search_desc})?"
        try:
            confirmed = Confirm.ask(prompt_msg, default=False)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operation aborted by user.[/yellow]")
            return 0
        if not confirmed:
            console.print("[yellow]Lidarr sync cancelled by user.[/yellow]")
            return 0

    summary: LidarrSyncSummary
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        apply_task = progress.add_task("Applying Lidarr plans...", total=len(plans_to_apply))

        def on_apply_progress(completed: int, total: int, plan: LidarrAlbumPlan) -> None:
            progress.advance(apply_task, 1)

        summary = apply_lidarr_plans(
            client=lidarr_client,
            plans=plans_to_apply,
            root_folder=resolved_root_folder,
            quality_profile_id=resolved_quality_profile,
            metadata_profile_id=resolved_metadata_profile,
            tag=lidarr_config.tag,
            trigger_search=args.lidarr_search,
            progress_callback=on_apply_progress,
        )

    search_status = (
        f"[green]Yes (Command ID: {summary.search_command_id})[/green]"
        if summary.search_triggered
        else "No"
    )
    summary_lines = [
        f"[bold]New Artists Added:[/] [green]{len(summary.added_artists)}[/green]",
        f"[bold]Albums Monitored:[/] [green]{len(summary.monitored_albums)}[/green]",
        f"[bold]Tag Applied:[/] [cyan]{lidarr_config.tag}[/cyan] ({len(summary.tagged_artist_ids)} artist(s) tagged)",
        f"[bold]Search Triggered:[/] {search_status}",
    ]
    if summary.failed:
        summary_lines.append(f"[bold red]Errors ({len(summary.failed)}):[/bold red]")
        for item, err in summary.failed:
            summary_lines.append(f"  • [red]{item}:[/red] {err}")

    console.print()
    console.print(
        Panel.fit(
            "\n".join(summary_lines),
            title="[bold green]Lidarr Synchronization Summary[/bold green]",
            border_style="green" if not summary.failed else "yellow",
        )
    )
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    """Build and return command line argument parser."""
    parser = argparse.ArgumentParser(
        description="Clean, modular CLI to import Spotify/Exportify playlists into Navidrome via Subsonic API.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  sync_playlist.py https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M\n"
            "  sync_playlist.py ./my_playlist.csv --name 'My Synthwave Mix'\n"
            "  sync_playlist.py 37i9dQZF1DXcBWIGoYBM5M --threshold 85 --update\n"
            "  sync_playlist.py ./rock.csv --dry-run --export-missing missing_rock.txt\n"
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        default=None,
        help="Spotify Playlist URL/ID or path to an Exportify CSV file.",
    )
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="Custom target playlist name on Navidrome (overrides default/derived name).",
    )
    parser.add_argument(
        "--threshold",
        type=parse_threshold,
        default=80,
        help="Fuzzy match threshold from 0 to 100 (default: 80).",
    )
    parser.add_argument(
        "--workers",
        type=parse_workers,
        default=4,
        help="Number of concurrent worker threads for matching (default: 4).",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="If a playlist with the target name exists on Navidrome, overwrite its tracks without prompting.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and match tracks and print summary without modifying anything on Navidrome.",
    )
    parser.add_argument(
        "--export-missing",
        type=str,
        default=None,
        metavar="PATH",
        help="Export unmatched tracks to a .csv or .txt file based on file extension.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print match score and matched Navidrome metadata for every individual track.",
    )
    parser.add_argument(
        "--send-to-lidarr",
        action="store_true",
        help="Resolve unmatched tracks to albums and send them to Lidarr.",
    )
    parser.add_argument(
        "--lidarr-search",
        action="store_true",
        help="Immediately trigger an AlbumSearch command in Lidarr for all newly monitored albums.",
    )
    parser.add_argument(
        "--lidarr-min-tracks",
        type=positive_int,
        default=1,
        help="Only send an album to Lidarr if at least N missing tracks belong to it (default: 1).",
    )
    parser.add_argument(
        "--lidarr-select",
        action="store_true",
        help="Interactively choose which resolved albums to send to Lidarr before applying changes.",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Skip interactive confirmation prompts.",
    )
    return parser


def main() -> int:
    """Main CLI entry point."""
    parser = build_arg_parser()
    args = parser.parse_args()

    # 1. Source acquisition
    source = args.source
    if not source:
        try:
            source = Prompt.ask("[bold cyan]Enter Spotify Playlist URL/ID or path to Exportify CSV[/bold cyan]")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Operation aborted by user.[/yellow]")
            return 0

    if not source or not source.strip():
        console.print("[bold red]Error: No source provided.[/bold red]")
        return 1

    source = source.strip()

    # 2. Check Navidrome credentials and connection
    try:
        url, user, password = load_navidrome_credentials()
    except ValueError as exc:
        console.print(f"[bold red]Configuration Error:[/bold red] {exc}")
        return 1

    client = NavidromeClient(url=url, user=user, password=password)

    console.print(f"[dim]Connecting to Navidrome at {url} (user: {user})...[/dim]")
    try:
        client.ping()
        console.print("[green]✔ Navidrome connection verified.[/green]")
    except (ConnectionError, SubsonicError) as exc:
        console.print(f"[bold red]Failed to connect to Navidrome:[/] {exc}")
        return 1

    # 3. Parse input source
    try:
        playlist_data = detect_and_load_source(source, custom_name=args.name)
    except Exception as exc:
        console.print(f"[bold red]Failed to parse playlist source:[/] {exc}")
        return 1

    if playlist_data.warnings:
        for w in playlist_data.warnings:
            console.print(f"\n[bold yellow]⚠ Warning: {w}[/bold yellow]\n")

    if not playlist_data.tracks:
        console.print(f"[bold yellow]Warning: Playlist '{playlist_data.name}' contains 0 tracks.[/bold yellow]")
        return 0

    target_name = args.name or playlist_data.name
    console.print(
        Panel.fit(
            f"[bold]Target Playlist:[/] {target_name}\n"
            f"[bold]Source Mode:[/] {playlist_data.source_type}\n"
            f"[bold]Total Tracks:[/] {len(playlist_data.tracks)}\n"
            f"[bold]Match Threshold:[/] {args.threshold}%\n"
            f"[bold]Workers:[/] {args.workers}\n"
            f"[bold]Dry Run:[/] {'Yes' if args.dry_run else 'No'}",
            title="[bold green]Playlist Sync[/bold green]",
            border_style="green",
        )
    )

    # 4. Match tracks against Navidrome
    results: list[MatchResult] = []
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("({task.completed}/{task.total})"),
        TimeElapsedColumn(),
        console=console,
    ) as progress:
        task_id = progress.add_task(f"Matching tracks ({args.threshold}% threshold)...", total=len(playlist_data.tracks))

        def on_track_match(idx: int, total: int, track: Track, result: MatchResult) -> None:
            progress.advance(task_id, 1)
            if args.verbose:
                if result.matched:
                    console.print(
                        f"  [bold green]✓[/bold green] [dim]({result.score:.1f}%)[/dim] "
                        f"{track.artist_display} - {track.title} "
                        f"[cyan]→[/cyan] {result.navidrome_artist} - {result.navidrome_title}"
                    )
                else:
                    score_info = f"best score: {result.score:.1f}%" if result.score > 0 else "no candidates"
                    console.print(
                        f"  [bold red]✗[/bold red] [dim]({score_info})[/dim] "
                        f"{track.artist_display} - {track.title}"
                    )

        results = match_playlist_tracks(
            client=client,
            tracks=playlist_data.tracks,
            threshold=float(args.threshold),
            progress_callback=on_track_match,
            max_workers=args.workers,
        )

    # 5. Display summary
    matched, unmatched = display_results_summary(target_name, results, verbose=args.verbose)

    # 6. Export missing tracks if requested
    if args.export_missing:
        if unmatched:
            export_unmatched_tracks(unmatched, args.export_missing)
        else:
            console.print("[green]All tracks were matched! Nothing to export.[/green]")

    # 7. Apply playlist changes to Navidrome (unless dry-run)
    if not args.dry_run:
        if matched:
            matched_song_ids = [str(r.navidrome_id) for r in matched if r.navidrome_id]

            try:
                existing_playlist = client.find_playlist_by_name(target_name)
            except Exception as exc:
                console.print(f"[yellow]Warning: Could not check existing playlists: {exc}[/yellow]")
                existing_playlist = None

            playlist_id_to_update: str | None = None

            if existing_playlist:
                existing_id = str(existing_playlist.get("id"))
                existing_count = existing_playlist.get("songCount", 0)

                if args.update or args.yes:
                    console.print(
                        f"[cyan]Existing playlist '{target_name}' found (ID: {existing_id}, {existing_count} tracks). "
                        f"Updating...[/cyan]"
                    )
                    playlist_id_to_update = existing_id
                else:
                    console.print(
                        f"\n[bold yellow]Notice: Playlist '{target_name}' already exists on Navidrome "
                        f"(ID: {existing_id}, current songs: {existing_count}).[/bold yellow]"
                    )
                    try:
                        should_overwrite = Confirm.ask("Do you want to overwrite it with the matched tracks?", default=False)
                    except (KeyboardInterrupt, EOFError):
                        console.print("\n[yellow]Aborted by user.[/yellow]")
                        return 0

                    if should_overwrite:
                        playlist_id_to_update = existing_id
                    else:
                        console.print("[yellow]Playlist sync cancelled by user (use --name to specify a different name).[/yellow]")
                        if not args.send_to_lidarr:
                            return 0

            if playlist_id_to_update is not None or not existing_playlist:
                with console.status(f"[bold green]Saving playlist '{target_name}' to Navidrome...", spinner="dots"):
                    try:
                        client.create_or_update_playlist(
                            name=target_name,
                            song_ids=matched_song_ids,
                            playlist_id=playlist_id_to_update,
                        )
                        action_word = "Updated" if playlist_id_to_update else "Created"
                        console.print(f"\n[bold green]✔ Successfully {action_word} playlist '{target_name}' with {len(matched_song_ids)} tracks![/bold green]")
                    except Exception as exc:
                        console.print(f"[bold red]Failed to save playlist to Navidrome:[/] {exc}")
                        if not args.send_to_lidarr:
                            return 1
        else:
            console.print("\n[yellow]No tracks matched. Playlist creation skipped on Navidrome.[/yellow]")
    else:
        console.print("\n[bold cyan]ℹ Dry run mode enabled: No playlists were created or modified on Navidrome.[/bold cyan]")

    # 8. Lidarr integration for unmatched tracks
    if args.send_to_lidarr:
        lidarr_ret = handle_lidarr_integration(unmatched, args)
        if lidarr_ret != 0:
            return lidarr_ret

    return 0


if __name__ == "__main__":
    sys.exit(main())
