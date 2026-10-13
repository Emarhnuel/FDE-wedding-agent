from types import SimpleNamespace
from pathlib import Path

import pytest

from src import rag
from src.schemas import AgentContext
from src.tools.knowledge_tools import search_company_knowledge

PDF_PATH = Path(__file__).resolve().parent.parent / "juniper-hollow-knowledge-pack.pdf"
COMPANY_ID = "juniper-hollow"


@pytest.fixture(scope="module")
def indexed_store(tmp_path_factory: pytest.TempPathFactory):
    chroma_dir = tmp_path_factory.mktemp("chroma_rag_test")
    rag.CHROMA_DIRECTORY = str(chroma_dir)
    rag.CHROMA_COLLECTION = "juniper_knowledge_test"
    rag.get_vector_store.cache_clear()

    chunk_count = rag.index_document(
        PDF_PATH,
        company_id=COMPANY_ID,
        source_id="JH-KB-2026-03",
        version="3.0",
        title="Juniper Hollow Company Knowledge Pack",
        status="published",
    )
    assert chunk_count > 0
    yield rag.get_vector_store()
    rag.get_vector_store.cache_clear()


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
