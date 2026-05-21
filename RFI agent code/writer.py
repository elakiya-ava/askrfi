"""
Excel writer — creates a filled copy of the RFI with answers, confidence scores, and color-coding.
"""

from __future__ import annotations

import os
import shutil
from typing import Optional

import openpyxl
from openpyxl.styles import PatternFill, Font, Alignment
from openpyxl.utils import get_column_letter


# Color scheme for confidence levels
FILL_GREEN = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")   # ≥ 0.80
FILL_YELLOW = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")  # 0.50–0.79
FILL_RED = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")     # < 0.50
FILL_HEADER = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
FONT_HEADER = Font(color="FFFFFF", bold=True)


def _confidence_fill(confidence: float) -> PatternFill:
    if confidence >= 0.80:
        return FILL_GREEN
    elif confidence >= 0.50:
        return FILL_YELLOW
    else:
        return FILL_RED


def write_filled_rfi(
    source_path: str,
    questions: list[dict],
    output_dir: Optional[str] = None,
) -> str:
    """
    Create a filled copy of the RFI Excel file.

    - Writes generated answers into the answer column
    - Adds a "Confidence" column with scores
    - Color-codes answer cells by confidence level
    - Returns the path to the output file
    """
    basename = os.path.splitext(os.path.basename(source_path))[0]
    ext = os.path.splitext(source_path)[1]
    out_dir = output_dir or os.path.dirname(source_path)
    output_path = os.path.join(out_dir, f"{basename}_FILLED{ext}")

    # Copy the original file to preserve all formatting, macros, etc.
    shutil.copy2(source_path, output_path)

    # Open the copy for editing (NOT read-only)
    # keep_vba=True only for .xlsm files (preserves macros);
    # .xlsx files must NOT contain VBA or Excel will reject them
    is_macro_file = ext.lower() == ".xlsm"
    wb = openpyxl.load_workbook(output_path, keep_vba=is_macro_file)

    # Group questions by sheet
    by_sheet = {}
    for q in questions:
        sheet = q["sheet_name"]
        if sheet not in by_sheet:
            by_sheet[sheet] = []
        by_sheet[sheet].append(q)

    for sheet_name, sheet_qs in by_sheet.items():
        if sheet_name not in wb.sheetnames:
            continue

        ws = wb[sheet_name]

        # Find or create the confidence column
        # Use the column after the answer column, or the last used column + 1
        answer_cols = set(q.get("answer_col") for q in sheet_qs if q.get("answer_col"))
        if answer_cols:
            # Use the first answer column found
            ans_col_letter = list(answer_cols)[0]
            ans_col_num = openpyxl.utils.column_index_from_string(ans_col_letter)
            conf_col_num = ans_col_num + 1
        else:
            conf_col_num = (ws.max_column or 1) + 1

        conf_col_letter = get_column_letter(conf_col_num)

        # Add confidence header
        # Find the header row (first row with questions for this sheet)
        min_row = min(q["row"] for q in sheet_qs)
        header_row = max(1, min_row - 1)

        try:
            conf_header_cell = ws[f"{conf_col_letter}{header_row}"]
            conf_header_cell.value = "AI Confidence"
            conf_header_cell.fill = FILL_HEADER
            conf_header_cell.font = FONT_HEADER
            conf_header_cell.alignment = Alignment(horizontal="center")
        except (AttributeError, TypeError):
            pass  # Skip if merged

        # Write answers and confidence scores
        for q in sheet_qs:
            row = q["row"]
            answer = q.get("generated_answer", "")
            confidence = q.get("confidence", 0)

            try:
                # Write answer (only if we generated one and the cell is currently empty or we're more confident)
                if answer and q.get("answer_col"):
                    answer_cell = ws[f"{q['answer_col']}{row}"]
                    # Only overwrite if the cell is empty or the existing answer is short/placeholder
                    existing = str(answer_cell.value or "").strip()
                    if not existing or existing.lower() in ("", "n/a", "tbd", "pending"):
                        answer_cell.value = answer
                        answer_cell.fill = _confidence_fill(confidence)
                    elif answer and not answer.startswith("[ERROR]"):
                        # Cell already has an answer — keep existing, just add color
                        answer_cell.fill = _confidence_fill(min(confidence, 0.95))

                # Write confidence score
                conf_cell = ws[f"{conf_col_letter}{row}"]
                conf_cell.value = round(confidence, 2)
                conf_cell.fill = _confidence_fill(confidence)
                conf_cell.alignment = Alignment(horizontal="center")
                conf_cell.number_format = "0%"
            except (AttributeError, TypeError):
                # Skip merged cells or other non-writable cells
                continue

    wb.save(output_path)
    wb.close()

    return output_path


def write_summary(
    questions: list[dict],
    output_path: str,
    source_filename: str,
    client_name: str = "",
) -> str:
    """
    Generate a markdown summary report of the fill results.
    """
    total = len(questions)
    filled = sum(1 for q in questions if q.get("generated_answer") and not q["generated_answer"].startswith("["))
    high_conf = sum(1 for q in questions if q.get("confidence", 0) >= 0.80)
    medium_conf = sum(1 for q in questions if 0.50 <= q.get("confidence", 0) < 0.80)
    low_conf = sum(1 for q in questions if q.get("confidence", 0) < 0.50)
    flagged = [q for q in questions if q.get("review_flag")]

    # Sheet breakdown
    sheet_counts = {}
    for q in questions:
        sheet = q.get("sheet_name", "Unknown")
        if sheet not in sheet_counts:
            sheet_counts[sheet] = {"total": 0, "filled": 0}
        sheet_counts[sheet]["total"] += 1
        if q.get("generated_answer") and not q["generated_answer"].startswith("["):
            sheet_counts[sheet]["filled"] += 1

    fill_rate = (filled / total * 100) if total > 0 else 0

    lines = [
        f"# RFI Fill Summary",
        f"",
        f"- **Source:** {source_filename}",
        f"- **Client:** {client_name or 'Unknown'}",
        f"- **Total questions:** {total}",
        f"- **Filled:** {filled} ({fill_rate:.0f}%)",
        f"- **Confidence breakdown:**",
        f"  - 🟢 High (≥80%): {high_conf}",
        f"  - 🟡 Medium (50-79%): {medium_conf}",
        f"  - 🔴 Low (<50%): {low_conf}",
        f"",
        f"## By Sheet",
        f"",
        f"| Sheet | Total | Filled | Rate |",
        f"|---|---|---|---|",
    ]

    for sheet, counts in sorted(sheet_counts.items()):
        rate = (counts["filled"] / counts["total"] * 100) if counts["total"] > 0 else 0
        lines.append(f"| {sheet} | {counts['total']} | {counts['filled']} | {rate:.0f}% |")

    if flagged:
        lines.extend([
            f"",
            f"## Flagged for Review ({len(flagged)})",
            f"",
        ])
        for q in flagged:
            lines.append(f"- **[{q.get('sheet_name')}] Row {q.get('row')}**: {q['question_text'][:100]}")
            lines.append(f"  - Flag: {q['review_flag']}")
            lines.append(f"  - Confidence: {q.get('confidence', 0):.0%}")
            lines.append(f"")

    # Needs review section (low confidence, not flagged)
    needs_review = [q for q in questions if q.get("confidence", 0) < 0.50 and not q.get("review_flag")]
    if needs_review:
        lines.extend([
            f"",
            f"## Needs Manual Answer ({len(needs_review)})",
            f"",
        ])
        for q in needs_review[:20]:  # Cap at 20
            lines.append(f"- **[{q.get('sheet_name')}] Row {q.get('row')}**: {q['question_text'][:100]}")
            lines.append(f"")

    text = "\n".join(lines)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(text)

    return output_path
