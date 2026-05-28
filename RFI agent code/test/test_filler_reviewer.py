"""
Filler + Reviewer breakage tests.

10 tests in increasing toughness targeting match_and_fill() and review_answers().
Azure AI Search is mocked out (patched to return False).
LLM calls are intercepted with controllable dummy responses.
Zero network calls occur.
"""

import json
import sys
import os
from unittest.mock import patch, MagicMock
from types import ModuleType

import pytest

# Add parent dir to path so we can import agents
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Pre-mock modules that have uninstalled deps (pymupdf) BEFORE any imports
# This prevents ImportError when patch() tries to import base_info_parser
_mock_base_info = ModuleType("base_info_parser")
_mock_base_info.load_base_info = lambda: {}
_mock_indexer = ModuleType("indexer")
_mock_indexer.load_knowledge_base = lambda: {}

sys.modules.setdefault("fitz", ModuleType("fitz"))
sys.modules.setdefault("base_info_parser", _mock_base_info)
# indexer imports are fine but let's ensure it doesn't trigger Azure SDK issues
if "indexer" not in sys.modules:
    sys.modules["indexer"] = _mock_indexer


# ─── FIXTURES ────────────────────────────────────────────────────────────────

DUMMY_KB = {
    "by_category": {
        "Company Information": [
            {
                "question": "What does your company do?",
                "answer": "Avalere Health is a healthcare consulting firm.",
                "client": "Pfizer",
                "source": "past_rfi_01.xlsx",
            }
        ],
        "Uncategorized": [],
    }
}

DUMMY_BASE_INFO = {
    "Company Information": "Avalere Health is a healthcare consulting and advisory firm.",
}


def _make_valid_llm_response(answer="Test answer", confidence=0.85, sources=None):
    """Helper: build a valid JSON string that _llm_call should return."""
    return json.dumps({
        "answer": answer,
        "confidence": confidence,
        "citation": "test_source.xlsx, Sheet1",
        "sources": sources or ["test_source.xlsx"],
    })


def _make_question(text="What is Avalere Health?", category="Company Information", existing_answer=""):
    """Helper: build a well-formed question dict."""
    return {
        "question_text": text,
        "category": category,
        "existing_answer": existing_answer,
    }


@pytest.fixture(autouse=True)
def mock_azure_and_loaders():
    """Patch Azure + lazy loaders for ALL tests. No network calls."""
    with patch("agents._azure_configured", return_value=False), \
         patch("agents._find_base_info_azure", return_value=""):
        yield


# ─── TEST 1: BASELINE HAPPY PATH ────────────────────────────────────────────

def test_basic_fill_single_question():
    """
    Difficulty: 1/10
    One well-formed question, LLM returns valid JSON.
    Should populate generated_answer, confidence, source_references.
    """
    from agents import match_and_fill

    questions = [_make_question()]
    valid_response = _make_valid_llm_response()

    with patch("agents._llm_call", return_value=valid_response):
        result = match_and_fill(questions, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 1
    assert result[0]["generated_answer"] == "Test answer"
    assert result[0]["confidence"] == 0.85
    assert result[0]["source_references"] == ["test_source.xlsx"]
    assert result[0]["citation"] == "test_source.xlsx, Sheet1"


# ─── TEST 2: EMPTY LIST ─────────────────────────────────────────────────────

def test_fill_empty_list():
    """
    Difficulty: 2/10
    Empty question list. Should return [] without calling LLM.
    """
    from agents import match_and_fill

    with patch("agents._llm_call") as mock_llm:
        result = match_and_fill([], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert result == []
    mock_llm.assert_not_called()


# ─── TEST 3: MISSING FIELDS ─────────────────────────────────────────────────

def test_fill_missing_fields():
    """
    Difficulty: 3/10
    Question dict missing 'category' and has minimal fields.
    Code does q.get("category", "Uncategorized") — should not crash.
    But does it crash on missing 'question_text'?
    """
    from agents import match_and_fill

    # Missing category (should default to Uncategorized)
    q_no_category = {"question_text": "Tell me about your team"}
    # Missing question_text entirely — this is the dangerous one
    q_no_text = {"category": "Company Information"}

    valid_response = _make_valid_llm_response()

    with patch("agents._llm_call", return_value=valid_response):
        # Test missing category — should not crash
        result = match_and_fill([q_no_category], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)
        assert len(result) == 1
        assert "generated_answer" in result[0]

    with patch("agents._llm_call", return_value=valid_response):
        # Test missing question_text — might crash with KeyError
        result = match_and_fill([q_no_text], knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)
        assert len(result) == 1
        # If it gets here without KeyError, the code handles it gracefully


# ─── TEST 4: LLM RETURNS MALFORMED JSON ─────────────────────────────────────

def test_llm_returns_malformed_json():
    """
    Difficulty: 4/10
    LLM returns plain text instead of JSON (e.g. "Sorry, I can't help with that").
    json.loads will fail — should produce [ERROR] gracefully, not crash.
    """
    from agents import match_and_fill

    questions = [_make_question()]

    with patch("agents._llm_call", return_value="Sorry, I cannot assist with that request."):
        result = match_and_fill(questions, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 1
    assert "[ERROR]" in result[0]["generated_answer"]
    assert result[0]["confidence"] == 0.0


# ─── TEST 5: LLM RETURNS PARTIAL JSON ───────────────────────────────────────

def test_llm_returns_partial_json():
    """
    Difficulty: 5/10
    LLM returns valid JSON but missing 'confidence', 'sources', 'citation' keys.
    Code uses .get() with defaults — verify it doesn't crash and defaults are sane.
    """
    from agents import match_and_fill

    questions = [_make_question()]
    partial_response = json.dumps({"answer": "Partial answer only"})

    with patch("agents._llm_call", return_value=partial_response):
        result = match_and_fill(questions, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 1
    assert result[0]["generated_answer"] == "Partial answer only"
    # confidence should default to 0 (since .get("confidence", 0) → float(0) → clamped)
    assert result[0]["confidence"] == 0.0
    # sources should default to empty list
    assert result[0]["source_references"] == []
    # citation should default to empty string
    assert result[0]["citation"] == ""


# ─── TEST 6: CONFIDENCE OUT OF RANGE ────────────────────────────────────────

def test_confidence_out_of_range():
    """
    Difficulty: 6/10
    LLM returns confidence values outside [0, 1]: 5.0 and -1.0.
    Code has: min(max(float(result.get("confidence", 0)), 0), 1.0)
    Verify clamping works.
    """
    from agents import match_and_fill

    # Test confidence = 5.0 (should clamp to 1.0)
    q_high = [_make_question("Q1")]
    with patch("agents._llm_call", return_value=_make_valid_llm_response(confidence=5.0)):
        result = match_and_fill(q_high, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)
    assert result[0]["confidence"] == 1.0

    # Test confidence = -1.0 (should clamp to 0.0)
    q_low = [_make_question("Q2")]
    with patch("agents._llm_call", return_value=_make_valid_llm_response(confidence=-1.0)):
        result = match_and_fill(q_low, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)
    assert result[0]["confidence"] == 0.0

    # Test confidence = "not a number" — float() will raise ValueError
    q_nan = [_make_question("Q3")]
    with patch("agents._llm_call", return_value=json.dumps({
        "answer": "test", "confidence": "high", "sources": []
    })):
        result = match_and_fill(q_nan, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)
    # Should hit except block → [ERROR]
    assert "[ERROR]" in result[0]["generated_answer"] or result[0]["confidence"] == 0.0


# ─── TEST 7: REVIEWER FLAGS CONTRADICTION ───────────────────────────────────

def test_review_flags_contradiction():
    """
    Difficulty: 7/10
    Two answers in same category. Reviewer LLM reports a contradiction.
    Verify: confidence capped at 0.5, review_status="flagged".
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "How many employees?",
            "category": "Company Information",
            "generated_answer": "We have 500 employees.",
            "confidence": 0.9,
        },
        {
            "question_text": "What is your team size?",
            "category": "Company Information",
            "generated_answer": "Our team consists of 1200 people.",
            "confidence": 0.85,
        },
    ]

    review_response = json.dumps({
        "contradictions": [
            {"questions": [1, 2], "issue": "Employee count inconsistency: 500 vs 1200"}
        ],
        "flags": [],
    })

    with patch("agents._llm_call", return_value=review_response):
        result = review_answers(questions)

    # Both should be flagged
    assert result[0]["review_status"] == "flagged"
    assert result[1]["review_status"] == "flagged"
    # Confidence capped at 0.5 for contradictions
    assert result[0]["confidence"] <= 0.5
    assert result[1]["confidence"] <= 0.5
    # review_flag should mention the contradiction
    assert "500" in result[0]["review_flag"] or "inconsistency" in result[0]["review_flag"].lower()


# ─── TEST 8: REVIEWER LLM RETURNS GARBAGE ───────────────────────────────────

def test_review_llm_returns_garbage():
    """
    Difficulty: 8/10
    Reviewer LLM returns completely unparseable text.
    Code catches json.JSONDecodeError → marks all as "unreviewed", caps confidence at 0.6.
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "Describe your compliance program.",
            "category": "Compliance",
            "generated_answer": "We have a robust compliance program.",
            "confidence": 0.9,
        },
        {
            "question_text": "Who is your compliance officer?",
            "category": "Compliance",
            "generated_answer": "Our Chief Compliance Officer is Jane Smith.",
            "confidence": 0.8,
        },
    ]

    with patch("agents._llm_call", return_value="I'm not sure what you want me to review..."):
        result = review_answers(questions)

    # All should be marked unreviewed
    assert result[0]["review_status"] == "unreviewed"
    assert result[1]["review_status"] == "unreviewed"
    # Confidence capped at 0.6
    assert result[0]["confidence"] <= 0.6
    assert result[1]["confidence"] <= 0.6


# ─── TEST 9: 100 QUESTIONS BATCH PROCESSING ─────────────────────────────────

def test_fill_100_questions_batch():
    """
    Difficulty: 9/10
    100 questions through the batch_size=5 loop.
    Verifies: all 100 get filled, no off-by-one, no silent drops.
    Also checks that LLM is called exactly 100 times (once per question).
    """
    from agents import match_and_fill

    questions = [_make_question(f"Question #{i}") for i in range(100)]
    valid_response = _make_valid_llm_response()

    with patch("agents._llm_call", return_value=valid_response) as mock_llm:
        result = match_and_fill(questions, knowledge_base=DUMMY_KB, base_info=DUMMY_BASE_INFO)

    assert len(result) == 100
    # Every question should have been filled
    for i, q in enumerate(result):
        assert "generated_answer" in q, f"Question #{i} missing generated_answer"
        assert q["generated_answer"] == "Test answer", f"Question #{i} has wrong answer"
    # LLM called exactly 100 times (once per question)
    assert mock_llm.call_count == 100


# ─── TEST 10: AUTH ERROR MUST BUBBLE UP ──────────────────────────────────────

def test_review_auth_error_bubbles():
    """
    Difficulty: 10/10
    _llm_call raises an exception containing "authentication" in the message.
    The reviewer code explicitly re-raises auth errors — verify it's NOT swallowed.
    This tests a security-critical path: if creds are bad, the user MUST know.
    """
    from agents import review_answers

    questions = [
        {
            "question_text": "Q1",
            "category": "Compliance",
            "generated_answer": "Answer 1",
            "confidence": 0.9,
        },
        {
            "question_text": "Q2",
            "category": "Compliance",
            "generated_answer": "Answer 2",
            "confidence": 0.8,
        },
    ]

    auth_error = Exception("authentication failed: invalid API key")

    with patch("agents._llm_call", side_effect=auth_error):
        with pytest.raises(Exception, match="authentication"):
            review_answers(questions)

    # Also test rate limit errors bubble up
    rate_error = Exception("rate limit exceeded, please retry after 60s")

    with patch("agents._llm_call", side_effect=rate_error):
        with pytest.raises(Exception, match="rate"):
            review_answers(questions)
