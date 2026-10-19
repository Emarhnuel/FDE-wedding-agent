from types import SimpleNamespace
from pathlib import Path
from uuid import uuid4

import pytest

from src import database, rag
from src.schemas import AgentContext
from src.tools.knowledge_tools import search_company_knowledge

PDF_PATH = Path(__file__).resolve().parent.parent / "juniper-hollow-knowledge-pack.pdf"
COMPANY_ID = f"test-rag-{uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def indexed_store():
    """Index the knowledge pack into pgvector for a throwaway company, publish it, and clean up."""
    database.init_schema()
    chunk_count = rag.index_document(
        PDF_PATH,
        company_id=COMPANY_ID,
        source_id="JH-KB-2026-03",
        version="3.0",
        title="Juniper Hollow Company Knowledge Pack",
    )
    assert chunk_count > 0
    document = database.insert_record(
        "documents", {"company_id": COMPANY_ID, "source_id": "JH-KB-2026-03", "version": "3.0", "status": "published"}
    )
    yield document
    for table in ("knowledge_chunks", "documents"):
        database.run_raw_sql(f"DELETE FROM {table} WHERE company_id = '{COMPANY_ID}';")


@pytest.mark.parametrize(
    ("question", "expected_terms"),
    [
        (
            "How many guests can Hollow Brook Barn and The Juniper Glasshouse hold?",
            ["180", "220", "96", "120"],
        ),
        (
            "Where do guests park at The Juniper Glasshouse and what does it cost?",
            ["Emerson Garage", "$8"],
        ),
        (
            "Can we bring our own alcohol or use an outside caterer at Hollow Brook Barn?",
            ["BYO alcohol is not permitted", "$600"],
        ),
        (
            "Can you hold a date for us, and what is required to reserve a wedding date?",
            ["courtesy holds", "30% retainer"],
        ),
    ],
)
def test_rag_answers_four_venue_questions(indexed_store, question: str, expected_terms: list[str]):
    docs = rag.search_knowledge(question, company_id=COMPANY_ID, k=4)
    assert docs, f"No documents returned for question: {question}"

    combined_text = "\n".join(doc.page_content for doc in docs)
    for term in expected_terms:
        assert term in combined_text, f"Expected {term!r} in retrieved chunks for: {question}"


def test_search_company_knowledge_tool_and_company_isolation(indexed_store):
    runtime = SimpleNamespace(context=AgentContext(company_id=COMPANY_ID))
    result = search_company_knowledge.func("How do we book a tour of the venues?", runtime=runtime)

    assert "[source: JH-KB-2026-03, version 3.0, page " in result
    assert "Tuesday to Saturday" in result

    other_company_docs = rag.search_knowledge(
        "How do we book a tour of the venues?",
        company_id="other-company",
        k=4,
    )
    assert other_company_docs == []


def test_only_published_versions_are_searchable(indexed_store):
    question = "How many guests can Hollow Brook Barn hold?"
    try:
        database.update_records("documents", {"status": "superseded"}, id=f"eq.{indexed_store['id']}")
        assert rag.search_knowledge(question, company_id=COMPANY_ID) == []
    finally:
        database.update_records("documents", {"status": "published"}, id=f"eq.{indexed_store['id']}")
    assert rag.search_knowledge(question, company_id=COMPANY_ID)
