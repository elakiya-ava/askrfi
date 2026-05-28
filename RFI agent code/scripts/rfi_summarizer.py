#!/usr/bin/env python3
"""Create a structured summary of each RFI file."""

import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "rfi_extracted")

def summarize_file(filepath):
    """Read a file and return first 3000 chars + sheet names."""
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Get sheet/slide names
    sections = []
    for line in content.split('\n'):
        if line.startswith('=== SHEET:') or line.startswith('=== SLIDE'):
            sections.append(line.strip())
    
    return {
        'total_chars': len(content),
        'sections': sections,
        'preview': content[:5000]
    }

def main():
    files = sorted(os.listdir(OUT_DIR))
    for fname in files:
        filepath = os.path.join(OUT_DIR, fname)
        info = summarize_file(filepath)
        print(f"\n{'='*80}")
        print(f"FILE: {fname}")
        print(f"Size: {info['total_chars']} chars | Sections: {len(info['sections'])}")
        print(f"Sections: {'; '.join(info['sections'][:15])}")
        print(f"{'='*80}")
        print(info['preview'][:3000])
        print("..." if info['total_chars'] > 3000 else "")

if __name__ == "__main__":
    main()
