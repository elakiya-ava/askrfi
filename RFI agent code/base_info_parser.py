"""
Base info parser — converts PDFs and HTMLs from the base info folder into clean text files.
Run once to populate data/base_info/ with .txt versions.
"""

import os
import fitz  # pymupdf
from bs4 import BeautifulSoup


def parse_pdf(path: str) -> str:
    """Extract text from a PDF file."""
    doc = fitz.open(path)
    pages = []
    for page in doc:
        pages.append(page.get_text())
    doc.close()
    return "\n\n".join(pages).strip()


def parse_html(path: str) -> str:
    """Extract visible text from a SharePoint HTML export."""
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        html = f.read()
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "meta", "link", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")

    # Clean up: collapse blank lines, strip whitespace
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def parse_base_info(base_info_dir: str, output_dir: str) -> dict[str, str]:
    """
    Parse all PDFs and HTMLs in base_info_dir into plain text.
    Saves .txt files to output_dir and returns {category_name: text_content}.
    """
    os.makedirs(output_dir, exist_ok=True)
    results = {}

    for fname in sorted(os.listdir(base_info_dir)):
        fpath = os.path.join(base_info_dir, fname)
        if not os.path.isfile(fpath):
            continue

        name_no_ext = os.path.splitext(fname)[0]
        ext = os.path.splitext(fname)[1].lower()

        if ext == ".pdf":
            text = parse_pdf(fpath)
        elif ext in (".html", ".htm"):
            text = parse_html(fpath)
        else:
            continue

        if not text or len(text) < 50:
            print(f"  WARN: {fname} produced very little text ({len(text)} chars), skipping")
            continue

        # Save to output
        out_path = os.path.join(output_dir, f"{name_no_ext}.txt")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(text)

        results[name_no_ext] = text
        print(f"  Parsed {fname} -> {len(text)} chars")

    return results


if __name__ == "__main__":
    script_dir = os.path.dirname(os.path.abspath(__file__))
    base_info_dir = os.path.join(script_dir, "..", "..", "base info")
    output_dir = os.path.join(script_dir, "data", "base_info")

    print("Parsing base info documents...")
    results = parse_base_info(base_info_dir, output_dir)
    print(f"\nDone. Parsed {len(results)} documents into {output_dir}")
    for name, text in results.items():
        print(f"  {name}: {len(text)} chars")
