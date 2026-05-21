"""
Run the full pipeline (upload → fill-mock → download) for each test file.
Reports success/failure and any errors encountered.
"""

import json
import os
import sys
import time
import requests

API_BASE = "http://localhost:8000"
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

TEST_FILES = [
    "test1_multi_sheet.xlsx",
    "test2_empty_rows_merged.xlsx",
    "test3_no_headers_special_chars.xlsx",
    "test4_hidden_protected_sheets.xlsx",
    "test5_large_malformed_unicode.xlsx",
]


def run_test(filename: str) -> dict:
    """Run upload → fill-mock → download for a single file."""
    filepath = os.path.join(TEST_DIR, filename)
    result = {
        "file": filename,
        "upload": {"status": "not_run", "detail": ""},
        "fill": {"status": "not_run", "detail": ""},
        "download_excel": {"status": "not_run", "detail": ""},
        "download_csv": {"status": "not_run", "detail": ""},
        "questions_parsed": 0,
        "sheets_detected": [],
    }

    # 1. Upload
    try:
        with open(filepath, "rb") as f:
            res = requests.post(
                f"{API_BASE}/api/upload",
                files={"file": (filename, f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
            )
        if res.status_code == 200:
            data = res.json()
            result["upload"]["status"] = "pass"
            result["upload"]["detail"] = f"{data['question_count']} questions parsed"
            result["questions_parsed"] = data["question_count"]
            session_id = data["session_id"]
            # Track which sheets were detected
            sheets = set(q["sheet_name"] for q in data["questions"])
            result["sheets_detected"] = sorted(sheets)
        else:
            result["upload"]["status"] = "fail"
            detail = res.json().get("detail", res.text) if res.headers.get("content-type", "").startswith("application/json") else res.text
            result["upload"]["detail"] = f"HTTP {res.status_code}: {detail}"
            return result
    except Exception as e:
        result["upload"]["status"] = "error"
        result["upload"]["detail"] = str(e)
        return result

    # 2. Fill (mock)
    try:
        res = requests.get(f"{API_BASE}/api/fill-mock/{session_id}", stream=True)
        if res.status_code == 200:
            # Consume SSE stream
            events = []
            for line in res.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        events.append(json.loads(line[5:].strip()))
                    except json.JSONDecodeError:
                        pass
            # Check if we got a 'done' event or all progress events
            filled_count = sum(1 for e in events if e.get("status") == "filled")
            result["fill"]["status"] = "pass"
            result["fill"]["detail"] = f"{filled_count}/{result['questions_parsed']} filled"
        else:
            result["fill"]["status"] = "fail"
            result["fill"]["detail"] = f"HTTP {res.status_code}: {res.text[:200]}"
            return result
    except Exception as e:
        result["fill"]["status"] = "error"
        result["fill"]["detail"] = str(e)
        return result

    # 3. Download Excel
    try:
        res = requests.get(f"{API_BASE}/api/download/{session_id}")
        if res.status_code == 200:
            # Save file
            dl_path = os.path.join(TEST_DIR, f"output_{filename}")
            with open(dl_path, "wb") as f:
                f.write(res.content)
            size_kb = len(res.content) / 1024
            result["download_excel"]["status"] = "pass"
            result["download_excel"]["detail"] = f"Downloaded {size_kb:.1f}KB → output_{filename}"
        else:
            result["download_excel"]["status"] = "fail"
            detail = ""
            try:
                detail = res.json().get("detail", "")
            except:
                detail = res.text[:200]
            result["download_excel"]["detail"] = f"HTTP {res.status_code}: {detail}"
    except Exception as e:
        result["download_excel"]["status"] = "error"
        result["download_excel"]["detail"] = str(e)

    # 4. Download CSV
    try:
        res = requests.get(f"{API_BASE}/api/download-csv/{session_id}")
        if res.status_code == 200:
            dl_path = os.path.join(TEST_DIR, f"output_{filename.replace('.xlsx', '.csv')}")
            with open(dl_path, "wb") as f:
                f.write(res.content)
            result["download_csv"]["status"] = "pass"
            result["download_csv"]["detail"] = f"Downloaded CSV OK"
        else:
            result["download_csv"]["status"] = "fail"
            result["download_csv"]["detail"] = f"HTTP {res.status_code}"
    except Exception as e:
        result["download_csv"]["status"] = "error"
        result["download_csv"]["detail"] = str(e)

    return result


def main():
    print("=" * 70)
    print("RFI AGENT — INTEGRATION TEST SUITE")
    print("=" * 70)

    # Health check
    try:
        res = requests.get(f"{API_BASE}/api/health")
        if res.status_code != 200:
            print(f"ERROR: Backend not healthy (HTTP {res.status_code})")
            sys.exit(1)
        print(f"✓ Backend healthy at {API_BASE}")
    except requests.ConnectionError:
        print(f"ERROR: Cannot connect to backend at {API_BASE}")
        print("Start the backend with: uvicorn api:app --port 8000")
        sys.exit(1)

    print()
    results = []

    for i, filename in enumerate(TEST_FILES, 1):
        print(f"\n{'─' * 70}")
        print(f"TEST {i}: {filename}")
        print(f"{'─' * 70}")

        result = run_test(filename)
        results.append(result)

        # Print results
        for step in ["upload", "fill", "download_excel", "download_csv"]:
            status = result[step]["status"]
            icon = "✓" if status == "pass" else "✗" if status == "fail" else "⚠" if status == "error" else "–"
            print(f"  {icon} {step:18s} {status:6s}  {result[step]['detail']}")

        if result["sheets_detected"]:
            print(f"    Sheets: {', '.join(result['sheets_detected'])}")

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    total_pass = sum(1 for r in results if all(
        r[s]["status"] == "pass" for s in ["upload", "fill", "download_excel", "download_csv"]
    ))
    total_partial = sum(1 for r in results if any(
        r[s]["status"] == "pass" for s in ["upload", "fill", "download_excel", "download_csv"]
    ) and not all(
        r[s]["status"] == "pass" for s in ["upload", "fill", "download_excel", "download_csv"]
    ))
    total_fail = len(results) - total_pass - total_partial

    print(f"  Full pass: {total_pass}/5")
    print(f"  Partial:   {total_partial}/5")
    print(f"  Full fail: {total_fail}/5")

    # Return results for further processing
    return results


if __name__ == "__main__":
    results = main()
    # Save JSON results
    with open(os.path.join(TEST_DIR, "test_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nDetailed results saved to test/test_results.json")
