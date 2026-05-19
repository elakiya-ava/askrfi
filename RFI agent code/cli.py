#!/usr/bin/env python3
"""
RFI Agent CLI — auto-fill RFIs for Avalere Health.

Usage:
    python cli.py index                          # Index all past RFIs into knowledge base
    python cli.py fill <path-to-rfi.xlsx>        # Fill a new RFI
    python cli.py fill <path> --client Pfizer    # Fill with explicit client name
    python cli.py stats                          # Show knowledge base stats
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import asdict

import click
from dotenv import load_dotenv

# Load .env from the script's directory
script_dir = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(script_dir, ".env"))

from excel_parser import parse_rfi, extract_client_from_filename
from indexer import index_all_rfis
from base_info_parser import parse_base_info
from agents import match_and_fill_async, review_answers
from writer import write_filled_rfi, write_summary


DATA_DIR = os.path.join(script_dir, "data")
KB_PATH = os.path.join(DATA_DIR, "knowledge_base.json")
BASE_INFO_DIR = os.path.join(DATA_DIR, "base_info")
RFI_LIBRARY_DIR = os.path.join(script_dir, "..")


@click.group()
def cli():
    """RFI Agent — auto-fill RFIs for Avalere Health."""
    pass


@cli.command()
def index():
    """Index all past RFIs and base info into the knowledge base."""
    click.echo("=" * 60)
    click.echo("Step 1: Parsing base info documents...")
    click.echo("=" * 60)
    base_info_source = os.path.join(script_dir, "..", "..", "base info")
    if os.path.isdir(base_info_source):
        parse_base_info(base_info_source, BASE_INFO_DIR)
    else:
        click.echo(f"  WARN: Base info directory not found at {base_info_source}")

    click.echo()
    click.echo("=" * 60)
    click.echo("Step 2: Indexing past RFIs...")
    click.echo("=" * 60)
    index_all_rfis(RFI_LIBRARY_DIR, KB_PATH)
    click.echo()
    click.echo("Done! Knowledge base is ready.")


@cli.command()
def stats():
    """Show knowledge base statistics."""
    if not os.path.exists(KB_PATH):
        click.echo("Knowledge base not found. Run 'index' first.")
        return

    with open(KB_PATH, "r") as f:
        kb = json.load(f)

    s = kb["stats"]
    click.echo(f"Knowledge Base Statistics")
    click.echo(f"{'=' * 40}")
    click.echo(f"Total RFIs indexed:     {s['total_rfis']}")
    click.echo(f"Total questions:        {s['total_questions']}")
    click.echo(f"Total answered Q&As:    {s['total_answered']}")
    click.echo()
    click.echo("By category:")
    for cat, count in sorted(s["by_category"].items(), key=lambda x: -x[1]):
        click.echo(f"  {cat:40s} {count:4d}")

    # Base info stats
    click.echo()
    if os.path.isdir(BASE_INFO_DIR):
        files = [f for f in os.listdir(BASE_INFO_DIR) if f.endswith(".txt")]
        click.echo(f"Base info documents:    {len(files)}")
        for f in sorted(files):
            size = os.path.getsize(os.path.join(BASE_INFO_DIR, f))
            click.echo(f"  {f:50s} {size:6d} chars")


@cli.command()
@click.argument("rfi_path", type=click.Path(exists=True))
@click.option("--client", "-c", default="", help="Client name (auto-detected from filename if not provided)")
@click.option("--output-dir", "-o", default=None, help="Output directory (default: same as input)")
@click.option("--interactive/--no-interactive", "-i/-I", default=True, help="Live progress UI (default: on)")
@click.option("--concurrency", "-n", default=5, help="Max concurrent Claude calls (default: 5)")
def fill(rfi_path: str, client: str, output_dir: str, interactive: bool, concurrency: int):
    """Fill a new RFI with answers from the knowledge base."""

    # Validate
    if not os.environ.get("ANTHROPIC_API_KEY"):
        click.echo("ERROR: ANTHROPIC_API_KEY not set. Add it to .env or export it.")
        sys.exit(1)

    # Check Azure is configured (required for retrieval)
    from agents import _azure_configured
    try:
        if not _azure_configured():
            click.echo("ERROR: Azure AI Search not configured. Set AZURE_SEARCH_ENDPOINT, "
                       "AZURE_SEARCH_API_KEY, AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY in .env")
            sys.exit(1)
    except EnvironmentError as e:
        click.echo(f"ERROR: {e}")
        sys.exit(1)

    filename = os.path.basename(rfi_path)
    if not client:
        client = extract_client_from_filename(filename)

    click.echo(f"{'=' * 60}")
    click.echo(f"RFI Agent — Filling: {filename}")
    click.echo(f"Client: {client or 'Unknown'}")
    click.echo(f"{'=' * 60}")

    # Step 1: Parse the RFI
    click.echo("\n[1/3] Parsing RFI...")
    start = time.time()
    questions = parse_rfi(rfi_path)
    questions = [asdict(q) if not isinstance(q, dict) else q for q in questions]
    click.echo(f"  Found {len(questions)} questions across {len(set(q['sheet_name'] for q in questions))} sheets")
    click.echo(f"  ({time.time() - start:.1f}s)")

    if not questions:
        click.echo("No questions found. Check the Excel file format.")
        sys.exit(1)

    # Step 2: Match and fill (Azure AI Search retrieval + Claude generation)
    click.echo("\n[2/3] Matching past answers and generating responses...")
    start = time.time()
    if interactive:
        from ui import run_fill_ui
        questions = run_fill_ui(questions, client_name=client, max_concurrent=concurrency)
    else:
        import asyncio
        questions = asyncio.run(match_and_fill_async(
            questions, client_name=client, max_concurrent=concurrency,
        ))
    filled = sum(1 for q in questions if q.get("generated_answer") and not q["generated_answer"].startswith("["))
    click.echo(f"  Filled {filled}/{len(questions)} questions")
    click.echo(f"  ({time.time() - start:.1f}s)")

    # Step 3: Review
    click.echo("\n[3/3] Reviewing answers for consistency...")
    start = time.time()
    questions = review_answers(questions)
    flagged = sum(1 for q in questions if q.get("review_status") == "flagged")
    click.echo(f"  Flagged {flagged} answers for review")
    click.echo(f"  ({time.time() - start:.1f}s)")

    # Write output
    click.echo("\nWriting output files...")
    start = time.time()
    out_dir = output_dir or os.path.dirname(rfi_path)

    excel_path = write_filled_rfi(rfi_path, questions, out_dir)
    click.echo(f"  Excel: {excel_path}")

    summary_path = os.path.join(
        out_dir,
        os.path.splitext(filename)[0] + "_SUMMARY.md"
    )
    write_summary(questions, summary_path, filename, client)
    click.echo(f"  Summary: {summary_path}")
    click.echo(f"  ({time.time() - start:.1f}s)")

    # Final stats
    high = sum(1 for q in questions if q.get("confidence", 0) >= 0.80)
    med = sum(1 for q in questions if 0.50 <= q.get("confidence", 0) < 0.80)
    low = sum(1 for q in questions if q.get("confidence", 0) < 0.50)

    click.echo(f"\n{'=' * 60}")
    click.echo(f"DONE!")
    click.echo(f"  Total: {len(questions)} questions")
    click.echo(f"  🟢 High confidence: {high}")
    click.echo(f"  🟡 Medium confidence: {med}")
    click.echo(f"  🔴 Low confidence / needs review: {low}")
    click.echo(f"{'=' * 60}")


if __name__ == "__main__":
    cli()
