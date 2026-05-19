"""
Interactive TUI for the RFI fill pipeline.

Uses `rich` to display a live-updating table grouped by sheet,
showing real-time progress as each question is filled.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from typing import Optional

from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.columns import Columns
from rich.spinner import Spinner


# --- STATUS ICONS -------------------------------------------------------------

_STATUS_MAP = {
    "pending": ("⏳", "dim"),
    "filling": ("🔄", "yellow"),
    "filled": ("✅", "green"),
    "truncated": ("⚠️ ", "yellow"),
    "rate_limited": ("🚫", "red"),
    "parse_error": ("❌", "red"),
    "error": ("❌", "red"),
}


def _status_text(status: str) -> Text:
    icon, style = _STATUS_MAP.get(status, ("?", "dim"))
    return Text(f" {icon} {status}", style=style)


def _confidence_text(conf: float) -> Text:
    if conf >= 0.8:
        return Text(f"{conf:.0%}", style="bold green")
    elif conf >= 0.5:
        return Text(f"{conf:.0%}", style="yellow")
    elif conf > 0:
        return Text(f"{conf:.0%}", style="red")
    return Text("—", style="dim")


def _truncate(text: str, max_len: int = 60) -> str:
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 1] + "…"


# --- TABLE BUILDER ------------------------------------------------------------

def _build_table(questions: list, elapsed: float) -> Table:
    """Build the progress table grouped by sheet."""
    # Count stats
    total = len(questions)
    done = sum(1 for q in questions if q.get("fill_status") not in ("pending", "filling"))
    errors = sum(1 for q in questions if q.get("fill_status") in ("error", "parse_error", "rate_limited"))

    title = (
        f"[bold]RFI Fill Progress[/bold]  "
        f"[green]{done}[/green]/{total} complete"
        f"  [red]{errors} errors[/red]" if errors else
        f"[bold]RFI Fill Progress[/bold]  "
        f"[green]{done}[/green]/{total} complete"
    )
    title += f"  ⏱ {elapsed:.1f}s"

    table = Table(
        title=title,
        show_lines=False,
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", width=4, justify="right", style="dim")
    table.add_column("Sheet", width=22, style="cyan", no_wrap=True)
    table.add_column("Question", min_width=40, ratio=2)
    table.add_column("Status", width=16, justify="center")
    table.add_column("Conf", width=6, justify="center")
    table.add_column("Answer Preview", min_width=30, ratio=1)

    # Group by sheet, preserving order
    sheets: OrderedDict[str, list] = OrderedDict()
    for i, q in enumerate(questions):
        sheet = q.get("sheet_name", "Unknown")
        if sheet not in sheets:
            sheets[sheet] = []
        sheets[sheet].append((i, q))

    row_num = 0
    for sheet, items in sheets.items():
        # Section header row
        table.add_row(
            "",
            Text(f"━━ {sheet} ━━", style="bold cyan"),
            "", "", "", "",
            style="on grey11" if row_num % 2 == 0 else None,
        )
        for i, q in items:
            row_num += 1
            status = q.get("fill_status", "pending")
            confidence = q.get("confidence", 0.0)
            answer = q.get("generated_answer", "")

            table.add_row(
                str(i + 1),
                "",
                _truncate(q.get("question_text", ""), 55),
                _status_text(status),
                _confidence_text(confidence) if status == "filled" else Text("—", style="dim"),
                Text(_truncate(answer, 45), style="dim") if answer else Text("", style="dim"),
            )

    return table


# --- MAIN RUNNER --------------------------------------------------------------

async def fill_with_live_ui(
    questions: list,
    client_name: str = "",
    max_concurrent: int = 5,
) -> list:
    """
    Run the async fill pipeline with a live-updating rich table.
    This is the main entry point for the interactive UI.
    """
    from agents import match_and_fill_async

    console = Console()
    start_time = time.time()

    # Mark all as pending initially
    for q in questions:
        q["fill_status"] = "pending"

    console.print()
    console.print(
        Panel(
            f"[bold]Starting RFI fill[/bold] — {len(questions)} questions, "
            f"max {max_concurrent} concurrent",
            style="blue",
        )
    )
    console.print()

    with Live(
        _build_table(questions, 0),
        console=console,
        refresh_per_second=4,
        vertical_overflow="visible",
    ) as live:

        async def progress_wrapper(q: dict) -> None:
            """Update live display after each question status changes."""
            elapsed = time.time() - start_time
            live.update(_build_table(questions, elapsed))

        # Run the fill
        result = await match_and_fill_async(
            questions,
            client_name=client_name,
            max_concurrent=max_concurrent,
            on_progress=progress_wrapper,
        )

        # Final refresh
        elapsed = time.time() - start_time
        live.update(_build_table(questions, elapsed))

    # Summary
    console.print()
    filled = sum(1 for q in questions if q.get("fill_status") == "filled")
    errors = sum(1 for q in questions if q.get("fill_status") in ("error", "parse_error", "rate_limited"))
    truncated = sum(1 for q in questions if q.get("fill_status") == "truncated")

    console.print(Panel(
        f"[bold green]Done![/bold green]  "
        f"✅ {filled} filled  "
        f"⚠️  {truncated} truncated  "
        f"❌ {errors} errors  "
        f"⏱ {elapsed:.1f}s total",
        style="green" if errors == 0 else "yellow",
    ))

    return result


def run_fill_ui(
    questions: list,
    client_name: str = "",
    max_concurrent: int = 5,
) -> list:
    """Sync wrapper — call this from cli.py."""
    return asyncio.run(
        fill_with_live_ui(
            questions, client_name=client_name, max_concurrent=max_concurrent,
        )
    )
