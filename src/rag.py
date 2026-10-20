import os
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from . import database

load_dotenv()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "google/gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768  # Must match knowledge_chunks.embedding in database.SCHEMA_SQL.
INSERT_BATCH_SIZE = 50  # Chunks per INSERT, keeping each request to the database small.

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)


def prepare_query(question: str) -> str:
    return f"task: search result | query: {question}"


def prepare_document(content: str, title: str | None = None) -> str:
    return f"title: {title or 'none'} | text: {content}"


@cache
def get_embeddings() -> OpenAIEmbeddings:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    # OpenAIEmbeddings is LangChain's OpenAI-compatible client; OpenRouter routes
    # these requests to google/gemini-embedding-2.
    return OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        dimensions=EMBEDDING_DIMENSIONS,
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )


def to_vector(values: list[float]) -> str:
    """pgvector's text form, cast with ::vector in SQL."""
    return "[" + ",".join(map(str, values)) + "]"


def load_pages(path: Path) -> list[Document]:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        pages = [page.extract_text() or "" for page in PdfReader(path).pages]
    elif suffix in {".txt", ".md"}:
        pages = [path.read_text(encoding="utf-8")]
    else:
        raise ValueError("Only PDF, TXT, and Markdown files are supported")
    return [
        Document(page_content=text, metadata={"page": number})
        for number, text in enumerate(pages, start=1)
        if text.strip()
    ]


def index_document(
    path: str | Path,
    *,
    company_id: str,
    source_id: str,
    version: str,
    title: str | None = None,
    venue: str = "",
) -> int:
    """Embed a file's chunks into knowledge_chunks and return the chunk count.

    Indexing never changes what the agent sees: chunks are searchable only while the matching
    `documents` row is published. Re-indexing the same version overwrites its chunks.
    """
    path = Path(path)
    chunks = text_splitter.split_documents(load_pages(path))
    if not chunks:
        raise ValueError(f"No readable text found in {path.name}")
    vectors = get_embeddings().embed_documents([prepare_document(c.page_content, title or source_id) for c in chunks])
    rows = [
        [f"{company_id}:{source_id}:{version}:{i}", company_id, source_id, version, venue, path.name,
         chunk.metadata["page"], chunk.page_content, to_vector(vector)]
        for i, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    for start in range(0, len(rows), INSERT_BATCH_SIZE):
        batch = rows[start:start + INSERT_BATCH_SIZE]
        values = ", ".join(
            "(" + ", ".join(f"${n * 9 + j}" for j in range(1, 9)) + f", ${n * 9 + 9}::vector)" for n in range(len(batch))
        )
        database.run_raw_sql(
            "INSERT INTO knowledge_chunks (id, company_id, source_id, version, venue, filename, page, content, embedding) "
            f"VALUES {values} ON CONFLICT (id) DO UPDATE SET content = EXCLUDED.content, embedding = EXCLUDED.embedding;",
            [value for row in batch for value in row],
        )
    return len(chunks)


def search_knowledge(query: str, company_id: str, k: int = 4) -> list[Document]:
    """Nearest chunks from the company's published document versions; the documents table decides what is live."""
    rows = database.run_raw_sql(
        "SELECT c.source_id, c.version, c.venue, c.filename, c.page, c.content FROM knowledge_chunks c "
        "JOIN documents d ON d.company_id = c.company_id AND d.source_id = c.source_id AND d.version = c.version "
        "WHERE c.company_id = $1 AND d.status = 'published' "
        "ORDER BY c.embedding <=> $2::vector LIMIT $3;",
        [company_id, to_vector(get_embeddings().embed_query(prepare_query(query))), k],
    )["rows"]
    return [Document(page_content=row.pop("content"), metadata={"company_id": company_id, **row}) for row in rows]
