"""
Aggressive test runner — targets specific failure modes.
"""

import json
import os
import sys
import requests

API_BASE = "http://localhost:8000"
TEST_DIR = os.path.dirname(os.path.abspath(__file__))

TESTS = [
    {
        "id": 1,
        "name": "Numeric-only sheets parsed as questions (Parser Bug)",
        "file": "test1_numeric_sheets_bug.xlsx",
        "severity": "Easy",
        "expect": "Financials and Scoring Matrix sheets should NOT produce questions",
    },
    {
        "id": 2,
        "name": "Corrupted non-Excel file (Error Handling)",
        "files": ["test2_corrupted_not_excel.xlsx", "test2b_truncated_excel.xlsx"],
        "severity": "Easy-Medium",
        "expect": "API should return 422 with clear error message, not 500",
    },
    {
        "id": 3,
        "name": "Writer stress — wide sheets + special chars (Writer Bug)",
        "file": "test3_writer_stress.xlsx",
        "severity": "Medium",
        "expect": "Writer should handle 20-col sheets and special sheet names without crash",
    },
    {
        "id": 4,
        "name": "Fake .xlsm macro file (Parser/Writer Path)",
        "file": "test4_fake_macro.xlsm",
        "severity": "Hard",
        "expect": "Parser opens with read_only=True, writer uses keep_vba=True — may crash",
    },
    {
        "id": 5,
        "name": "Zero questions / edge session states",
        "files": ["test5a_no_questions.xlsx"],
        "severity": "Complex",
        "expect": "Should get 422 'No questions found'; also test download-before-fill",
    },
]


def run_pipeline(filepath: str, filename: str) -> dict:
    """Full pipeline: upload → fill-mock → download."""
    result = {
        "file": filename,
        "upload": {"status": "not_run", "detail": "", "http_code": None},
        "fill": {"status": "not_run", "detail": ""},
        "download": {"status": "not_run", "detail": ""},
        "questions_parsed": 0,
        "sheets_detected": [],
        "questions_detail": {},
    }

    # Upload
    try:
        with open(filepath, "rb") as f:
            res = requests.post(
                f"{API_BASE}/api/upload",
                files={"file": (filename, f, "application/octet-stream")},
            )
        result["upload"]["http_code"] = res.status_code
        if res.status_code == 200:
            data = res.json()
            result["upload"]["status"] = "pass"
            result["upload"]["detail"] = f"{data['question_count']} questions"
            result["questions_parsed"] = data["question_count"]
            session_id = data["session_id"]
            # Detailed breakdown by sheet
            from collections import Counter
            sheet_counts = Counter(q["sheet_name"] for q in data["questions"])
            result["sheets_detected"] = sorted(sheet_counts.keys())
            result["questions_detail"] = dict(sheet_counts)
        elif res.status_code == 422:
            result["upload"]["status"] = "expected_error"
            detail = res.json().get("detail", "") if "json" in res.headers.get("content-type", "") else res.text
            result["upload"]["detail"] = f"422: {detail}"
            return result
        else:
            result["upload"]["status"] = "fail"
            result["upload"]["detail"] = f"HTTP {res.status_code}: {res.text[:200]}"
            return result
    except Exception as e:
        result["upload"]["status"] = "error"
        result["upload"]["detail"] = str(e)
        return result

    # Fill mock
    try:
        res = requests.get(f"{API_BASE}/api/fill-mock/{session_id}", stream=True)
        if res.status_code == 200:
            filled = 0
            for line in res.iter_lines(decode_unicode=True):
                if line and line.startswith("data:"):
                    try:
                        evt = json.loads(line[5:].strip())
                        if evt.get("status") == "filled":
                            filled += 1
                    except json.JSONDecodeError:
                        pass
            result["fill"]["status"] = "pass"
            result["fill"]["detail"] = f"{filled}/{result['questions_parsed']} filled"
        else:
            result["fill"]["status"] = "fail"
            result["fill"]["detail"] = f"HTTP {res.status_code}"
    except Exception as e:
        result["fill"]["status"] = "error"
        result["fill"]["detail"] = str(e)

    # Download
    try:
        res = requests.get(f"{API_BASE}/api/download/{session_id}")
        if res.status_code == 200:
            dl_path = os.path.join(TEST_DIR, f"output_{filename}")
            with open(dl_path, "wb") as f:
                f.write(res.content)
            result["download"]["status"] = "pass"
            result["download"]["detail"] = f"{len(res.content)/1024:.1f}KB"
        else:
            result["download"]["status"] = "fail"
            try:
                detail = res.json().get("detail", "")
            except:
                detail = res.text[:200]
            result["download"]["detail"] = f"HTTP {res.status_code}: {detail}"
    except Exception as e:
        result["download"]["status"] = "error"
        result["download"]["detail"] = str(e)

    return result


def test_download_before_fill():
    """Test downloading before filling — should get 400 error."""
    # Upload a valid file
    filepath = os.path.join(TEST_DIR, "test1_numeric_sheets_bug.xlsx")
    with open(filepath, "rb") as f:
        res = requests.post(
            f"{API_BASE}/api/upload",
            files={"file": ("test_premature_dl.xlsx", f, "application/octet-stream")},
        )
    if res.status_code != 200:
        return {"status": "error", "detail": "Upload failed"}
    
    session_id = res.json()["session_id"]
    # Try download immediately (without fill)
    res = requests.get(f"{API_BASE}/api/download/{session_id}")
    if res.status_code == 400:
        return {"status": "pass", "detail": f"Correctly rejected with 400: {res.json().get('detail', '')}"}
    elif res.status_code == 200:
        return {"status": "fail", "detail": "ALLOWED download before fill — should be blocked!"}
    else:
        return {"status": "unexpected", "detail": f"HTTP {res.status_code}"}


def test_double_fill():
    """Test filling a session that's already being filled — should get 409."""
    filepath = os.path.join(TEST_DIR, "test1_numeric_sheets_bug.xlsx")
    with open(filepath, "rb") as f:
        res = requests.post(
            f"{API_BASE}/api/upload",
            files={"file": ("test_double_fill.xlsx", f, "application/octet-stream")},
        )
    if res.status_code != 200:
        return {"status": "error", "detail": "Upload failed"}
    
    session_id = res.json()["session_id"]
    
    # Start first fill (don't consume stream)
    import threading
    first_res = [None]
    def do_fill():
        first_res[0] = requests.get(f"{API_BASE}/api/fill-mock/{session_id}", stream=True)
    
    t = threading.Thread(target=do_fill)
    t.start()
    
    import time
    time.sleep(0.5)  # Let first fill start
    
    # Try second fill
    res = requests.get(f"{API_BASE}/api/fill-mock/{session_id}", stream=True)
    status_code = res.status_code
    
    # Clean up
    t.join(timeout=5)
    
    if status_code == 409:
        return {"status": "pass", "detail": "Correctly rejected duplicate fill with 409"}
    elif status_code == 200:
        return {"status": "fail", "detail": "ALLOWED concurrent fill — race condition!"}
    else:
        return {"status": "unexpected", "detail": f"HTTP {status_code}"}


def main():
    print("=" * 70)
    print("RFI AGENT — AGGRESSIVE TEST SUITE")
    print("=" * 70)

    # Health check
    try:
        res = requests.get(f"{API_BASE}/api/health", timeout=5)
        assert res.status_code == 200
        print(f"✓ Backend healthy at {API_BASE}\n")
    except Exception as e:
        print(f"ERROR: Backend not available: {e}")
        sys.exit(1)

    all_results = []

    # === TEST 1 ===
    print(f"\n{'─' * 70}")
    print(f"TEST 1: Numeric-only sheets parsed as questions [SEVERITY: Easy]")
    print(f"{'─' * 70}")
    r = run_pipeline(os.path.join(TEST_DIR, "test1_numeric_sheets_bug.xlsx"), "test1_numeric_sheets_bug.xlsx")
    all_results.append(r)
    
    # Validate: should only have questions from "Questions" sheet
    bug_sheets = [s for s in r["sheets_detected"] if s != "Questions"]
    if bug_sheets:
        print(f"  ✗ BUG CONFIRMED: Numeric sheets parsed as questions!")
        print(f"    Sheets with false positives: {bug_sheets}")
        print(f"    Questions per sheet: {r['questions_detail']}")
        r["bug"] = f"Numeric sheets {bug_sheets} incorrectly produced questions"
    else:
        print(f"  ✓ Only 'Questions' sheet parsed correctly")
    
    for step in ["upload", "fill", "download"]:
        status = r[step]["status"]
        icon = "✓" if status == "pass" else "✗" if status in ("fail", "error") else "⚠"
        print(f"  {icon} {step:12s} {r[step]['detail']}")

    # === TEST 2 ===
    print(f"\n{'─' * 70}")
    print(f"TEST 2: Corrupted non-Excel files [SEVERITY: Easy-Medium]")
    print(f"{'─' * 70}")
    for fname in ["test2_corrupted_not_excel.xlsx", "test2b_truncated_excel.xlsx"]:
        fpath = os.path.join(TEST_DIR, fname)
        r = run_pipeline(fpath, fname)
        all_results.append(r)
        status = r["upload"]["status"]
        icon = "✓" if status == "expected_error" else "✗"
        print(f"  {icon} {fname}: {r['upload']['detail']}")
        if status != "expected_error":
            r["bug"] = f"Expected 422 but got {r['upload']['http_code']}"

    # === TEST 3 ===
    print(f"\n{'─' * 70}")
    print(f"TEST 3: Writer stress — wide sheets + special chars [SEVERITY: Medium]")
    print(f"{'─' * 70}")
    r = run_pipeline(os.path.join(TEST_DIR, "test3_writer_stress.xlsx"), "test3_writer_stress.xlsx")
    all_results.append(r)
    for step in ["upload", "fill", "download"]:
        status = r[step]["status"]
        icon = "✓" if status == "pass" else "✗"
        print(f"  {icon} {step:12s} {r[step]['detail']}")
    if r["download"]["status"] != "pass":
        r["bug"] = f"Writer failed: {r['download']['detail']}"
    print(f"    Sheets: {r['sheets_detected']}")
    print(f"    Questions: {r['questions_detail']}")

    # === TEST 4 ===
    print(f"\n{'─' * 70}")
    print(f"TEST 4: Fake .xlsm macro file [SEVERITY: Hard]")
    print(f"{'─' * 70}")
    r = run_pipeline(os.path.join(TEST_DIR, "test4_fake_macro.xlsm"), "test4_fake_macro.xlsm")
    all_results.append(r)
    for step in ["upload", "fill", "download"]:
        status = r[step]["status"]
        icon = "✓" if status == "pass" else "✗"
        print(f"  {icon} {step:12s} {r[step]['detail']}")
    if r["download"]["status"] != "pass":
        r["bug"] = f"Writer crashed on .xlsm: {r['download']['detail']}"

    # === TEST 5 ===
    print(f"\n{'─' * 70}")
    print(f"TEST 5: Zero questions + edge session states [SEVERITY: Complex]")
    print(f"{'─' * 70}")
    
    # 5a: No questions file
    r = run_pipeline(os.path.join(TEST_DIR, "test5a_no_questions.xlsx"), "test5a_no_questions.xlsx")
    all_results.append(r)
    if r["upload"]["status"] == "expected_error":
        print(f"  ✓ No-questions file correctly rejected: {r['upload']['detail']}")
    else:
        print(f"  ✗ Expected 422 but got: {r['upload']}")
        r["bug"] = "File with no questions was accepted"
    
    # 5b: Download before fill
    print(f"  --- Download before fill ---")
    r2 = test_download_before_fill()
    print(f"  {'✓' if r2['status'] == 'pass' else '✗'} {r2['detail']}")
    all_results.append({"test": "download_before_fill", **r2})
    
    # 5c: Double fill (race condition)
    print(f"  --- Double fill (race condition) ---")
    r3 = test_double_fill()
    print(f"  {'✓' if r3['status'] == 'pass' else '✗'} {r3['detail']}")
    all_results.append({"test": "double_fill", **r3})

    # === SUMMARY ===
    print(f"\n{'=' * 70}")
    print("BUGS FOUND:")
    print(f"{'=' * 70}")
    bugs = [r for r in all_results if r.get("bug")]
    if bugs:
        for b in bugs:
            print(f"  • {b.get('file', b.get('test', '?'))}: {b['bug']}")
    else:
        print("  No bugs found!")
    
    # Save results
    with open(os.path.join(TEST_DIR, "aggressive_test_results.json"), "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    print(f"\nResults saved to test/aggressive_test_results.json")
    
    return all_results


if __name__ == "__main__":
    main()
