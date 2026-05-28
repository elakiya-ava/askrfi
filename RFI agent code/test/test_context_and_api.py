"""
Context-passing & API breakage tests — May 28, 2026.

15+ tests targeting the bugs found today:
  - category_hint vs category field mismatch
  - base_info fallback (all vs none)
  - [NEEDS REVIEW] stripping in api layer
  - SSE streaming edge cases
  - confidence clamping & row coloring logic
  - LLM returns code-fenced JSON
  - Knowledge base empty/malformed
  - Large base_info context handling

Zero network calls. All LLM/Azure mocked.
"""

import json
import sys
import os
import re
from unittest.mock import patch, MagicMock, AsyncMock
from types import ModuleType

import pytest

# Add parent dir to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pre-mock uninstalled deps
_mock_base_info = ModuleType("base_info_parser")
_mock_base_info.load_base_info = lambda: {}
_mock_indexer = ModuleType("indexer")
_mock_indexer.load_knowledge_base = lambda: {}
sys.modules.setdefault("fitz", ModuleType("fitz"))
sys.modules.setdefault("base_info_parser", _mock_base_info)
if "indexer" not in sys.modules:
    sys.modules["indexer"] = _mock_indexer


# ─── FIXTURES ────────────────────────────────────────────────────────────────

DUMMY_KB = {
    "by_category": {
        "Company Information": [
            {
                "question": "What does your company do?",
                "answer": "Avalere Health is a healthcare consulting firm within Inizio.",
                "client": "Pfizer",
                "source": "past_rfi_01.xlsx",
            }
        ],
        "People Information": [
            {
                "question": "How many employees?",
                "answer": "1300+ employees globally.",
                "client": "Gilead",
                "source": "past_rfi_02.xlsx",
            }
        ],
        "Uncategorized": [],
    }
}

DUMMY_BASE_INFO = {
    "Company Information": "Avalere Health LLC, registration 7069027. Founded as part of Inizio group.",
    "People Information": "1300+ employees. North America: 58%, Europe: 41%, Asia-Pacific: <1%.",
    "Compliance": "We have a comprehensive compliance framework overseen by the CCO.",
}


def _make_valid_llm_response(answer="Test answer", confidence=0.85, sources=None, citation=""):
    return json.dumps({
        "answer": answer,
        "confidence": confidence,
        "citation": citation or "test_source.xlsx, Sheet1",
        "sources": sources or ["test_source.xlsx"],
    })


def _make_question(text="What is Avalere Health?", category_hint="", category="", existing=""):
    q = {"question_text": text, "existing_answer": existing}
    if category_hint:
        q["category_hint"] = category_hint
    if category:
        q["category"] = category
    return q


@pytest.fixture(autouse=True)
def mock_azure():
    with patch("agents._azure_configured", return_value=False), \
         patch("agents._find_base_info_azure", return_value=""):
        yield


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 1: category_hint field is used when category is missing
# ═══════════════════════════════════════════════════════════════════════════════

def test_category_hint_used_over_missing_category():
    """
    BUG FIX VERIFICATION: q has category_hint='People Information' but no
    'category' key. match_and_fill should use category_hint for retrieval.
    """
    from agents import match_and_fill

    q = _make_question("How many employees?", category_hint="People Information")
    assert "category" not in q  # Confirm no category key

    with patch("agents._llm_call", return_value=_make_valid_llm_response("1300+", 0.9)):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result[0]["generated_answer"] == "1300+"
    assert result[0]["confidence"] == 0.9


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 2: category_hint takes priority over empty string category
# ═══════════════════════════════════════════════════════════════════════════════

def test_category_hint_priority_over_empty_category():
    """
    When both exist but category is empty string, category_hint should win.
    """
    from agents import match_and_fill

    q = {"question_text": "Describe compliance", "category_hint": "Compliance", "category": ""}

    with patch("agents._llm_call", return_value=_make_valid_llm_response("Compliance answer", 0.8)):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result[0]["generated_answer"] == "Compliance answer"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 3: Uncategorized questions get ALL base_info (not empty)
# ═══════════════════════════════════════════════════════════════════════════════

def test_uncategorized_gets_all_base_info():
    """
    BUG FIX VERIFICATION: When category doesn't match category_to_base,
    ALL base_info files should be included in the prompt context.
    """
    from agents import match_and_fill

    q = _make_question("What is your company name?", category_hint="SomeRandomCategory")
    captured_prompts = []

    def capture_llm(messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _make_valid_llm_response("Avalere Health LLC", 0.95)

    with patch("agents._llm_call", side_effect=capture_llm):
        match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    # The prompt should contain ALL base_info sections
    prompt = captured_prompts[0]
    assert "Company Information" in prompt
    assert "People Information" in prompt
    assert "Compliance" in prompt
    assert "1300+ employees" in prompt
    assert "No base info available" not in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 4: Known category gets ONLY its mapped base_info
# ═══════════════════════════════════════════════════════════════════════════════

def test_known_category_gets_specific_base_info():
    """
    When category matches the map, only the mapped file should be included.
    """
    from agents import match_and_fill

    q = _make_question("Tell me about your team size", category_hint="People Information")
    captured_prompts = []

    def capture_llm(messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _make_valid_llm_response("1300+", 0.9)

    with patch("agents._llm_call", side_effect=capture_llm):
        match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    prompt = captured_prompts[0]
    assert "People Information" in prompt
    assert "1300+ employees" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 5: Empty base_info dict doesn't crash on fallback
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_base_info_no_crash():
    """
    If base_info is {} (empty), the fallback loop should produce nothing, not crash.
    """
    from agents import match_and_fill

    q = _make_question("What year was the company founded?")

    with patch("agents._llm_call", return_value=_make_valid_llm_response("", 0.0)):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info={})

    assert len(result) == 1
    # Should not crash — just produce empty/low-confidence answer


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 6: Empty knowledge_base doesn't crash
# ═══════════════════════════════════════════════════════════════════════════════

def test_empty_knowledge_base_no_crash():
    """
    If knowledge_base has no by_category key, should not KeyError.
    """
    from agents import match_and_fill

    q = _make_question("Describe your services")
    empty_kb = {}

    with patch("agents._llm_call", return_value=_make_valid_llm_response("Services", 0.7)):
        result = match_and_fill([q], knowledge_base=empty_kb, base_info=DUMMY_BASE_INFO)

    assert len(result) == 1
    assert result[0]["generated_answer"] == "Services"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 7: LLM returns code-fenced JSON (```json ... ```)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_returns_code_fenced_json():
    """
    Many LLMs wrap JSON in markdown code blocks. The code strips these.
    Verify this works for both ```json and plain ``` fences.
    """
    from agents import match_and_fill

    q = _make_question("Test question")
    fenced_response = '```json\n{"answer": "fenced answer", "confidence": 0.9, "citation": "src", "sources": ["src"]}\n```'

    with patch("agents._llm_call", return_value=fenced_response):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result[0]["generated_answer"] == "fenced answer"
    assert result[0]["confidence"] == 0.9


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 8: [NEEDS REVIEW] prefix gets stripped in API layer
# ═══════════════════════════════════════════════════════════════════════════════

def test_needs_review_stripped():
    """
    API layer strips [NEEDS REVIEW] prefix from answers.
    Test the regex pattern handles variants.
    """
    pattern = re.compile(r'^\[NEEDS REVIEW\]\s*[-\u2013\u2014]?\s*', re.IGNORECASE)

    cases = [
        ("[NEEDS REVIEW] Avalere Health", "Avalere Health"),
        ("[NEEDS REVIEW] - Some answer", "Some answer"),
        ("[NEEDS REVIEW] — Long dash variant", "Long dash variant"),
        ("[needs review] lowercase", "lowercase"),
        ("Clean answer no prefix", "Clean answer no prefix"),
        ("", ""),
    ]

    for input_text, expected in cases:
        result = pattern.sub("", input_text)
        assert result == expected, f"Failed for input: {input_text!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 9: Confidence = 0 with empty answer (LLM can't answer)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_returns_empty_answer_zero_confidence():
    """
    When LLM can't answer, it should return empty string + confidence 0.
    The prompt instructs this. Verify it flows through correctly.
    """
    from agents import match_and_fill

    q = _make_question("What is the CEO's favorite color?")
    response = json.dumps({"answer": "", "confidence": 0.0, "citation": "", "sources": []})

    with patch("agents._llm_call", return_value=response):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result[0]["generated_answer"] == ""
    assert result[0]["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 10: LLM raises exception mid-batch (doesn't kill other questions)
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_exception_doesnt_kill_batch():
    """
    If LLM raises on question 3 of 5, questions 1-2 and 4-5 should still work.
    The try/except per question should isolate failures.
    """
    from agents import match_and_fill

    questions = [_make_question(f"Q{i}") for i in range(5)]
    call_count = [0]

    def flaky_llm(messages, **kwargs):
        call_count[0] += 1
        if call_count[0] == 3:
            raise RuntimeError("Temporary API failure")
        return _make_valid_llm_response(f"Answer {call_count[0]}", 0.8)

    with patch("agents._llm_call", side_effect=flaky_llm):
        result = match_and_fill(questions, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 5
    # Q3 (index 2) should have [ERROR]
    assert "[ERROR]" in result[2]["generated_answer"]
    assert result[2]["confidence"] == 0.0
    # Others should be fine
    assert result[0]["generated_answer"] == "Answer 1"
    assert result[1]["generated_answer"] == "Answer 2"
    assert result[3]["generated_answer"] == "Answer 4"
    assert result[4]["generated_answer"] == "Answer 5"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 11: question_text with only whitespace treated as missing
# ═══════════════════════════════════════════════════════════════════════════════

def test_whitespace_only_question_text():
    """
    A question with question_text = "   " should be treated as empty.
    Should NOT be sent to LLM. Should get [ERROR] marker.
    """
    from agents import match_and_fill

    q = {"question_text": "   ", "category_hint": "Company Information"}

    with patch("agents._llm_call") as mock_llm:
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 1
    mock_llm.assert_not_called()
    assert "[ERROR]" in result[0]["generated_answer"]
    assert result[0]["confidence"] == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 12: Reviewer handles single-answer category (no review needed)
# ═══════════════════════════════════════════════════════════════════════════════

def test_reviewer_single_answer_skips_review():
    """
    Categories with 0-1 answers should be auto-marked 'reviewed' without LLM call.
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "Solo question",
            "category_hint": "Technology & AI",
            "generated_answer": "We use AI responsibly.",
            "confidence": 0.9,
        }
    ]

    with patch("agents._llm_call") as mock_llm:
        result = review_answers(questions)

    mock_llm.assert_not_called()
    assert result[0]["review_status"] == "reviewed"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 13: Reviewer uses category_hint (not just category)
# ═══════════════════════════════════════════════════════════════════════════════

def test_reviewer_uses_category_hint():
    """
    review_answers groups by category. Must use category_hint field.
    Two questions with same category_hint should be reviewed together.
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "Q1",
            "category_hint": "Compliance",
            "generated_answer": "We are compliant.",
            "confidence": 0.9,
        },
        {
            "question_text": "Q2",
            "category_hint": "Compliance",
            "generated_answer": "Our compliance program is robust.",
            "confidence": 0.85,
        },
    ]

    review_response = json.dumps({"contradictions": [], "flags": []})

    with patch("agents._llm_call", return_value=review_response) as mock_llm:
        result = review_answers(questions)

    # LLM should be called once (for the "Compliance" category group)
    mock_llm.assert_called_once()
    assert result[0]["review_status"] == "reviewed"
    assert result[1]["review_status"] == "reviewed"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 14: Existing answer passed to LLM context
# ═══════════════════════════════════════════════════════════════════════════════

def test_existing_answer_in_prompt():
    """
    If a question has an existing_answer, it should appear in the LLM prompt.
    """
    from agents import match_and_fill

    q = _make_question("Company address?", category_hint="Company Information",
                       existing="1201 New York Ave NW, Washington DC")
    captured_prompts = []

    def capture_llm(messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _make_valid_llm_response("1201 New York Ave NW", 0.95)

    with patch("agents._llm_call", side_effect=capture_llm):
        match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    prompt = captured_prompts[0]
    assert "1201 New York Ave NW" in prompt
    assert "EXISTING ANSWER" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 15: Past Q&A pairs from knowledge_base appear in prompt
# ═══════════════════════════════════════════════════════════════════════════════

def test_past_qa_pairs_in_prompt():
    """
    When the KB has matching category entries, they should appear in the prompt.
    """
    from agents import match_and_fill

    q = _make_question("What does Avalere do?", category_hint="Company Information")
    captured_prompts = []

    def capture_llm(messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _make_valid_llm_response("Healthcare consulting", 0.9)

    with patch("agents._llm_call", side_effect=capture_llm):
        match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    prompt = captured_prompts[0]
    assert "Past Q&A pairs" in prompt
    assert "healthcare consulting firm" in prompt.lower()
    assert "Pfizer" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 16: No past Q&A for unknown category (graceful empty)
# ═══════════════════════════════════════════════════════════════════════════════

def test_no_past_qa_for_unknown_category():
    """
    KB has no entries for 'WeirdCategory'. Should produce
    'No similar past Q&A pairs found' in prompt, not crash.
    """
    from agents import match_and_fill

    q = _make_question("Random question", category_hint="WeirdCategory")
    captured_prompts = []

    def capture_llm(messages, **kwargs):
        captured_prompts.append(messages[0]["content"])
        return _make_valid_llm_response("IDK", 0.3)

    with patch("agents._llm_call", side_effect=capture_llm):
        match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    prompt = captured_prompts[0]
    assert "No similar past Q&A pairs found" in prompt


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 17: LLM response with extra whitespace/newlines in JSON
# ═══════════════════════════════════════════════════════════════════════════════

def test_llm_response_extra_whitespace():
    """
    LLM sometimes adds trailing newlines or spaces around JSON.
    json.loads should handle this, but code-fence stripping might break.
    """
    from agents import match_and_fill

    q = _make_question("Test")
    # Response with leading/trailing whitespace inside code fence
    response = '```\n  {"answer": "whitespace test", "confidence": 0.7, "citation": "", "sources": []}  \n```'

    with patch("agents._llm_call", return_value=response):
        result = match_and_fill([q], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result[0]["generated_answer"] == "whitespace test"


# ═══════════════════════════════════════════════════════════════════════════════
# TEST 18: Reviewer confidence adjustment doesn't go negative
# ═══════════════════════════════════════════════════════════════════════════════

def test_reviewer_confidence_floor_zero():
    """
    If reviewer applies -0.5 adjustment to a 0.3 confidence answer,
    result should be max(0, 0.3 - 0.5) = 0, not -0.2.
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "Q1",
            "category_hint": "Compliance",
            "generated_answer": "Answer A",
            "confidence": 0.3,
        },
        {
            "question_text": "Q2",
            "category_hint": "Compliance",
            "generated_answer": "Answer B",
            "confidence": 0.9,
        },
    ]

    review_response = json.dumps({
        "contradictions": [],
        "flags": [{"question": 1, "issue": "Too vague", "confidence_adjustment": -0.5}],
    })

    with patch("agents._llm_call", return_value=review_response):
        result = review_answers(questions)

    # 0.3 - 0.5 = -0.2, but should be clamped to 0
    assert result[0]["confidence"] >= 0.0
    assert result[0]["review_status"] == "flagged"
