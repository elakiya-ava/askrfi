"""
RFI pipeline functions that process RFI questions.

match_and_fill       — retrieves similar past Q&A + generates answers via LLM
match_and_fill_async — async concurrent version (for CLI/TUI)
review_answers       — checks consistency across answers via LLM

Retrieval uses Azure AI Search when configured, JSON fallback otherwise.
LLM provider is configurable via LLM_PROVIDER env var (anthropic or openai).
"""

from __future__ import annotations

import json
import os

from openai import AzureOpenAI, OpenAI
from azure.search.documents import SearchClient
from azure.search.documents.models import VectorizedQuery
from azure.core.credentials import AzureKeyCredential


# ─── LLM ABSTRACTION (provider-agnostic) ────────────────────────────────────

def _llm_call(
    messages: list[dict],
    temperature: float = 0.1,
    max_tokens: int = 2048,
) -> str:
    """
    Send a chat completion request to the configured LLM provider.
    Returns the response text.

    Supported providers (LLM_PROVIDER env var):
      - "anthropic" (default) — uses Anthropic Messages API
      - "openai" — uses OpenAI Chat Completions API (also works with any
        OpenAI-compatible endpoint: Azure OpenAI, Ollama, LM Studio, etc.
        Set LLM_BASE_URL for custom endpoints.)
    """
    provider = os.environ.get("LLM_PROVIDER", "anthropic").lower()
    model = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

    if provider == "anthropic":
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY"))
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return response.content[0].text.strip()

    elif provider == "openai":
        kwargs = {
            "api_key": os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        }
        base_url = os.environ.get("LLM_BASE_URL")
        if base_url:
            kwargs["base_url"] = base_url
        client = OpenAI(**kwargs)
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return response.choices[0].message.content.strip()

    elif provider == "azure_openai":
        client = OpenAI(
            api_key=os.environ.get("LLM_API_KEY") or os.environ.get("AZURE_OPENAI_LLM_API_KEY"),
            base_url=os.environ.get("LLM_BASE_URL") or os.environ.get("AZURE_OPENAI_LLM_ENDPOINT"),
            default_headers={"api-key": os.environ.get("LLM_API_KEY") or os.environ.get("AZURE_OPENAI_LLM_API_KEY")},
        )
        response = client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return response.choices[0].message.content.strip()

    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: '{provider}'. "
            f"Supported: 'anthropic', 'openai', 'azure_openai'"
        )




# ─── AZURE AI SEARCH + EMBEDDING HELPERS ────────────────────────────────────

def _get_azure_openai_client() -> AzureOpenAI:
    return AzureOpenAI(
        azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_API_KEY"],
        api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
    )


def _get_search_client(index_name: str) -> SearchClient:
    return SearchClient(
        endpoint=os.environ["AZURE_SEARCH_ENDPOINT"],
        index_name=index_name,
        credential=AzureKeyCredential(os.environ["AZURE_SEARCH_API_KEY"]),
    )


def _embed(text: str, aoai_client: AzureOpenAI | None = None) -> list[float]:
    """Embed text using Azure OpenAI. Returns a vector."""
    aoai_client = aoai_client or _get_azure_openai_client()
    deployment = os.environ.get("AZURE_OPENAI_EMBEDDING_DEPLOYMENT", "text-embedding-ada-002")
    response = aoai_client.embeddings.create(input=[text], model=deployment)
    return response.data[0].embedding


_AZURE_REQUIRED_VARS = [
    "AZURE_SEARCH_ENDPOINT",
    "AZURE_SEARCH_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_KEY",
]


def _azure_configured() -> bool:
    """Check if Azure AI Search env vars are set. Raises if partially configured."""
    set_vars = [v for v in _AZURE_REQUIRED_VARS if os.environ.get(v)]
    if len(set_vars) == len(_AZURE_REQUIRED_VARS):
        return True
    if set_vars:
        missing = [v for v in _AZURE_REQUIRED_VARS if v not in set_vars]
        raise EnvironmentError(
            f"Azure AI Search is partially configured. "
            f"Set vars: {set_vars}. Missing: {missing}"
        )
    return False


# ─── AGENT 2: MATCHER + FILLER ──────────────────────────────────────────────

def _find_similar_qas_azure(
    question: str,
    category: str,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    Azure AI Search hybrid retrieval: keyword + vector + semantic ranker.
    Returns ranked results with real similarity scores.
    """
    aoai_client = _get_azure_openai_client()
    embedding = _embed(question, aoai_client)

    index_name = os.environ.get("AZURE_SEARCH_QUESTION_INDEX", "rfi-questions")
    search_client = _get_search_client(index_name)

    # Hybrid query: keyword + vector + semantic ranker (no category filter)
    vector_query = VectorizedQuery(
        vector=embedding,
        k_nearest_neighbors=50,
        fields="embedding",
    )

    search_kwargs = {
        "search_text": question,
        "vector_queries": [vector_query],
        "top": max_results,
    }

    # Enable semantic ranker if a config is set (optional — requires Standard tier)
    semantic_config = os.environ.get("AZURE_SEARCH_SEMANTIC_CONFIG")
    if semantic_config:
        search_kwargs["query_type"] = "semantic"
        search_kwargs["semantic_configuration_name"] = semantic_config

    # Enable client boost scoring profile if configured
    scoring_profile = os.environ.get("AZURE_SEARCH_SCORING_PROFILE")
    if scoring_profile and client_name:
        search_kwargs["scoring_profile"] = scoring_profile
        search_kwargs["scoring_parameters"] = [f"clientName-{client_name}"]

    results = search_client.search(**search_kwargs)

    output = []
    for r in results:
        result_client = r.get("client", "")
        output.append({
            "question": r["question_text"],
            "answer": r["answer_text"],
            "client": result_client,
            "source": r.get("source_file", ""),
            "match_type": "same_client" if client_name and result_client.lower() == client_name.lower() else "same_category",
            "score": r.get("@search.score", 0.0),
        })

    return output


def _find_similar_qas_json(
    question: str,
    category: str,
    knowledge_base: dict,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    v1 fallback: filter JSON by category, boost same-client.
    No relevance ranking — returns in insertion order with client boost.
    """
    same_client = []
    other_client = []

    cat_qas = knowledge_base.get("by_category", {}).get(category, [])
    for qa in cat_qas:
        entry = {
            "question": qa["question"],
            "answer": qa["answer"],
            "client": qa.get("client", ""),
            "source": qa.get("source", ""),
            "match_type": "same_category",
        }
        if client_name and qa.get("client", "").lower() == client_name.lower():
            entry["match_type"] = "same_client"
            same_client.append(entry)
        else:
            other_client.append(entry)

    results = same_client + other_client

    if not results:
        for qa in knowledge_base.get("by_category", {}).get("Uncategorized", []):
            results.append({
                "question": qa["question"],
                "answer": qa["answer"],
                "client": qa.get("client", ""),
                "source": qa.get("source", ""),
                "match_type": "uncategorized",
            })

    return results[:max_results]


def _find_similar_qas(
    question: str,
    category: str,
    knowledge_base: dict,
    client_name: str = "",
    max_results: int = 10,
) -> list[dict]:
    """
    Retrieve similar Q&A pairs. Uses Azure AI Search when configured,
    falls back to JSON category filter otherwise.

    Return shape is stable regardless of backend:
    [{"question", "answer", "client", "source", "match_type", "score"?}, ...]
    """
    if _azure_configured():
        try:
            return _find_similar_qas_azure(question, category, client_name, max_results)
        except Exception as e:
            print(f"  WARN: Azure AI Search failed, falling back to JSON: {e}")

    return _find_similar_qas_json(question, category, knowledge_base, client_name, max_results)


def _find_base_info_azure(category: str, question: str) -> str:
    """
    Retrieve relevant base info chunks from Azure AI Search knowledge index.
    Returns formatted text to inject into the filler prompt.
    """
    if not _azure_configured():
        return ""

    index_name = os.environ.get("AZURE_SEARCH_KNOWLEDGE_INDEX", "rfi-knowledge")
    try:
        aoai_client = _get_azure_openai_client()
        embedding = _embed(question, aoai_client)
        search_client = _get_search_client(index_name)

        vector_query = VectorizedQuery(
            vector=embedding,
            k_nearest_neighbors=50,
            fields="embedding",
        )

        results = search_client.search(
            search_text=question,
            vector_queries=[vector_query],
            top=5,
        )

        chunks = []
        for r in results:
            heading = r.get("section_heading", "")
            source = r.get("source_doc", "")
            content = r.get("content", "")
            label = f"{source} > {heading}" if heading else source
            chunks.append(f"\n--- {label} ---\n{content}")

        return "\n".join(chunks) if chunks else ""

    except Exception as e:
        print(f"  WARN: Azure knowledge search failed: {e}")
        return ""


def match_and_fill(
    questions: list[dict],
    knowledge_base: dict | None = None,
    base_info: dict[str, str] | None = None,
    client_name: str = "",
) -> list[dict]:
    """
    For each question, find similar past answers and generate a response.
    Adds 'generated_answer', 'confidence', and 'source_references' to each question.

    knowledge_base and base_info are lazy-loaded if not provided.
    """
    # Lazy-load if not provided
    if knowledge_base is None:
        from indexer import load_knowledge_base
        knowledge_base = load_knowledge_base()
    if base_info is None:
        from base_info_parser import load_base_info
        base_info = load_base_info()

    # Map category names to base info keys
    category_to_base = {
        "Company Information": ["Company Information"],
        "Commercial Information": ["Commercial Information (General)"],
        "Compliance": ["Compliance"],
        "Legal": [],  # No base info yet
        "Data & Information Security": ["Data, information security, and client confidentiality"],
        "ESG": ["Environmental, social, and governance"],
        "People Information": ["People Information"],
        "Suppliers & Freelancers": ["Suppliers and freelancers"],
        "Technology & AI": ["Technology and AI"],
    }

    # Process in batches of 5 to balance speed and quality
    batch_size = 5
    for start in range(0, len(questions), batch_size):
        batch = questions[start:start + batch_size]

        for q in batch:
            category = q.get("category", "Uncategorized")

            # Gather context — past Q&A pairs
            similar_qas = _find_similar_qas(
                q["question_text"], category, knowledge_base, client_name
            )

            # Get relevant base info
            # Azure path: semantic search over chunked base info
            # JSON fallback: full text files by category mapping
            base_info_text = _find_base_info_azure(category, q["question_text"])
            if not base_info_text:
                for key in category_to_base.get(category, []):
                    if key in base_info:
                        base_info_text += f"\n--- {key} ---\n{base_info[key]}\n"

            # Build context for Claude
            past_qa_text = ""
            if similar_qas:
                past_qa_text = "\n\nPast Q&A pairs from similar RFIs:\n"
                for i, qa in enumerate(similar_qas):
                    past_qa_text += f"\n[Past Q{i+1} | Client: {qa['client']} | Source: {qa['source']}]\n"
                    past_qa_text += f"Q: {qa['question']}\n"
                    past_qa_text += f"A: {qa['answer']}\n"

            prompt = f"""You are filling out an RFI (Request for Information) for Avalere Health, a healthcare consulting firm.

QUESTION (Category: {category}):
{q['question_text']}

{f"EXISTING ANSWER (from previous submission): {q.get('existing_answer', '')}" if q.get('existing_answer') else "No existing answer."}

{f"BASE COMPANY INFORMATION:{base_info_text}" if base_info_text else "No base info available for this category."}
{past_qa_text if past_qa_text else "No similar past Q&A pairs found."}

INSTRUCTIONS:
1. If a past Q&A question is nearly identical to this question, reuse that past answer verbatim.
2. If similar (but not identical) past Q&A pairs are available, adapt the most relevant answer to fit this specific question.
3. If an existing answer is provided and is consistent with the base info and past Q&As, you may reuse it.
4. If base company information is available, use it to construct an accurate answer.
5. DO NOT fabricate, or hallucinate any facts, statistics, certifications, policy details, or any specifics not present in the provided context. If the context does not contain enough information to answer confidently, respond with "[NEEDS REVIEW]" followed by your best attempt using only what is provided.
6. Keep the tone professional and consistent with a healthcare consulting firm.
7. Be concise but thorough. Match the expected level of detail.
8. Add a citation for all the sources you used from the provided context 

Respond with ONLY a JSON object:
{{
  "answer": "your answer text",
  "confidence": 0.0-1.0,
  "citation":"cite sources used in this answer for ex: filename, section heading/sheet name, cell name etc",
  "sources": ["list of sources used"]
}}
"""
            try:
                text = _llm_call(
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=2048,
                )
                if text.startswith("```"):
                    text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                result = json.loads(text)
                q["generated_answer"] = result.get("answer", "")
                q["confidence"] = min(max(float(result.get("confidence", 0)), 0), 1.0)
                q["citation"] = result.get("citation", "")
                q["source_references"] = result.get("sources", [])

            except Exception as e:
                q["generated_answer"] = f"[ERROR] Could not generate answer: {e}"
                q["confidence"] = 0.0
                q["citation"] = ""
                q["source_references"] = []

        # Progress indicator
        done = min(start + batch_size, len(questions))
        print(f"  Filled {done}/{len(questions)} questions", end="\r")

    print()
    return questions


# ─── AGENT 3: REVIEWER ──────────────────────────────────────────────────────

def review_answers(
    questions: list[dict],
) -> list[dict]:
    """
    Review generated answers for consistency and quality.
    Adjusts confidence scores and flags issues.

    Every answer gets a 'review_status' field:
      - "reviewed"   — reviewer checked it, no issues
      - "flagged"    — reviewer found a concern
      - "unreviewed" — review failed or was skipped
    """

    # Group answers by category to check consistency
    by_category = {}
    for q in questions:
        cat = q.get("category", "Uncategorized")
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append(q)

    # Check for contradictions within each category
    for cat, cat_qs in by_category.items():
        answered = [q for q in cat_qs if q.get("generated_answer") and q["confidence"] > 0]

        # Categories with 0-1 answers: mark as reviewed (nothing to contradict)
        if len(answered) < 2:
            for q in answered:
                q["review_status"] = "reviewed"
            continue

        # Build a summary for Claude to review
        qa_summary = "\n".join(
            f"Q{i+1}: {q['question_text']}\nA{i+1}: {q['generated_answer']}\n"
            for i, q in enumerate(answered[:15])  # Cap at 15 to stay in context
        )

        try:
            text = _llm_call(
                messages=[{
                    "role": "user",
                    "content": f"""Review these RFI answers for Avalere Health in the "{cat}" category.
Check for:
1. Contradictions between answers (e.g., different employee counts, inconsistent company descriptions)
2. Factual errors or implausible claims
3. Answers that are too vague to be useful

Answers:
{qa_summary}

Respond with ONLY a JSON object:
{{
  "contradictions": [
    {{"questions": [1, 3], "issue": "description of contradiction"}}
  ],
  "flags": [
    {{"question": 2, "issue": "description of concern", "confidence_adjustment": -0.2}}
  ]
}}

If no issues found, respond with {{"contradictions": [], "flags": []}}
"""
                }],
                temperature=0,
                max_tokens=2048,
            )

            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            review = json.loads(text)

            # Start by marking all as reviewed (clean)
            for q in answered:
                q["review_status"] = "reviewed"

            # Apply confidence adjustments for flagged answers
            for flag in review.get("flags", []):
                q_idx = flag.get("question", 0) - 1
                if 0 <= q_idx < len(answered):
                    adj = float(flag.get("confidence_adjustment", 0))
                    answered[q_idx]["confidence"] = max(0, answered[q_idx]["confidence"] + adj)
                    answered[q_idx]["review_flag"] = flag.get("issue", "")
                    answered[q_idx]["review_status"] = "flagged"

            for contradiction in review.get("contradictions", []):
                for q_idx in contradiction.get("questions", []):
                    idx = q_idx - 1
                    if 0 <= idx < len(answered):
                        answered[idx]["review_flag"] = contradiction.get("issue", "Contradiction detected")
                        answered[idx]["confidence"] = min(answered[idx]["confidence"], 0.5)
                        answered[idx]["review_status"] = "flagged"

        except (json.JSONDecodeError, IndexError, KeyError, ValueError) as e:
            # Parse errors: review failed but not fatal — flag all as unreviewed
            print(f"  WARN: Review parse error for category '{cat}': {e}")
            for q in answered:
                q["review_status"] = "unreviewed"
                q["review_flag"] = f"Review could not parse response: {e}"
                # Penalize confidence — unreviewed answers are less trustworthy
                q["confidence"] = min(q["confidence"], 0.6)

        except Exception as e:
            # API errors (auth, rate limit, network): let auth/rate-limit bubble up,
            # treat other API errors as non-fatal
            error_str = str(e).lower()
            if "auth" in error_str or "api key" in error_str:
                raise  # Auth failures must not be swallowed
            if "rate" in error_str and "limit" in error_str:
                raise  # Rate limits must not be swallowed

            # Other API errors: flag as unreviewed
            print(f"  WARN: Review failed for category '{cat}': {e}")
            for q in answered:
                q["review_status"] = "unreviewed"
                q["review_flag"] = f"Review failed: {e}"
                q["confidence"] = min(q["confidence"], 0.6)

    # Mark any answers that never went through review (e.g., confidence was 0)
    for q in questions:
        if "review_status" not in q:
            q["review_status"] = "unreviewed"

    return questions


# ─── ASYNC FILL (for CLI/TUI with concurrency) ──────────────────────────────

async def match_and_fill_async(
    questions: list[dict],
    knowledge_base: dict | None = None,
    base_info: dict[str, str] | None = None,
    client_name: str = "",
    max_concurrent: int = 5,
    on_progress=None,
) -> list[dict]:
    """
    Async concurrent version of match_and_fill.
    Fills questions with up to max_concurrent parallel LLM calls.
    Calls on_progress(q) after each question is filled (if provided).
    """
    import asyncio
    import concurrent.futures

    # Lazy-load if not provided
    if knowledge_base is None:
        from indexer import load_knowledge_base
        knowledge_base = load_knowledge_base()
    if base_info is None:
        from base_info_parser import load_base_info
        base_info = load_base_info()

    # Category → base info key mapping
    category_to_base = {
        "Company Information": ["Company Information"],
        "Commercial Information": ["Commercial Information (General)"],
        "Compliance": ["Compliance"],
        "Legal": [],
        "Data & Information Security": ["Data, information security, and client confidentiality"],
        "ESG": ["Environmental, social, and governance"],
        "People Information": ["People Information"],
        "Suppliers & Freelancers": ["Suppliers and freelancers"],
        "Technology & AI": ["Technology and AI"],
    }

    semaphore = asyncio.Semaphore(max_concurrent)
    loop = asyncio.get_event_loop()
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_concurrent)

    def _fill_one(q: dict) -> None:
        """Sync function that fills a single question (runs in thread)."""
        category = q.get("category", q.get("category_hint", "Uncategorized"))

        similar_qas = _find_similar_qas(
            q["question_text"], category, knowledge_base, client_name
        )

        base_info_text = _find_base_info_azure(category, q["question_text"])
        if not base_info_text:
            for key in category_to_base.get(category, []):
                if key in base_info:
                    base_info_text += f"\n--- {key} ---\n{base_info[key]}\n"

        past_qa_text = ""
        if similar_qas:
            past_qa_text = "\n\nPast Q&A pairs from similar RFIs:\n"
            for i, qa in enumerate(similar_qas):
                past_qa_text += f"\n[Past Q{i+1} | Client: {qa['client']} | Source: {qa['source']}]\n"
                past_qa_text += f"Q: {qa['question']}\n"
                past_qa_text += f"A: {qa['answer']}\n"

        prompt = f"""You are filling out an RFI (Request for Information) for Avalere Health, a healthcare consulting firm.

QUESTION (Category: {category}):
{q['question_text']}

{f"EXISTING ANSWER (from previous submission): {q.get('existing_answer', '')}" if q.get('existing_answer') else "No existing answer."}

{f"BASE COMPANY INFORMATION:{base_info_text}" if base_info_text else "No base info available for this category."}
{past_qa_text if past_qa_text else "No similar past Q&A pairs found."}

INSTRUCTIONS:
1. If a past Q&A question is nearly identical to this question, reuse that past answer verbatim.
2. If similar (but not identical) past Q&A pairs are available, adapt the most relevant answer to fit this specific question.
3. If an existing answer is provided and is consistent with the base info and past Q&As, you may reuse it.
4. If base company information is available, use it to construct an accurate answer.
5. DO NOT fabricate, or hallucinate any facts, statistics, certifications, policy details, or any specifics not present in the provided context. If the context does not contain enough information to answer confidently, respond with "[NEEDS REVIEW]" followed by your best attempt using only what is provided.
6. Keep the tone professional and consistent with a healthcare consulting firm.
7. Be concise but thorough. Match the expected level of detail.
8. Add a citation for all the sources you used from the provided context

Respond with ONLY a JSON object:
{{
  "answer": "your answer text",
  "confidence": 0.0-1.0,
  "citation":"cite sources used in this answer for ex: filename, section heading/sheet name, cell name etc",
  "sources": ["list of sources used"]
}}
"""
        try:
            text = _llm_call(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=2048,
            )
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            result = json.loads(text)
            q["generated_answer"] = result.get("answer", "")
            q["confidence"] = min(max(float(result.get("confidence", 0)), 0), 1.0)
            q["citation"] = result.get("citation", "")
            q["source_references"] = result.get("sources", [])
            q["fill_status"] = "filled"

        except Exception as e:
            q["generated_answer"] = f"[ERROR] Could not generate answer: {e}"
            q["confidence"] = 0.0
            q["citation"] = ""
            q["source_references"] = []
            q["fill_status"] = "error"

    async def _fill_with_semaphore(q: dict) -> None:
        async with semaphore:
            q["fill_status"] = "filling"
            if on_progress:
                await on_progress(q)
            await loop.run_in_executor(executor, _fill_one, q)
            if on_progress:
                await on_progress(q)

    tasks = [_fill_with_semaphore(q) for q in questions]
    await asyncio.gather(*tasks)

    executor.shutdown(wait=False)
    return questions
