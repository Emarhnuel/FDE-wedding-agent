import os
import threading
from functools import cache
from pathlib import Path

from dotenv import load_dotenv
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

load_dotenv()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
EMBEDDING_MODEL = "google/gemini-embedding-2"
EMBEDDING_DIMENSIONS = 768
# Anchored to the project root so the index is found whatever folder the process starts in.
CHROMA_DIRECTORY = str(Path(__file__).resolve().parent.parent / ".data" / "chroma")
# Private originals of uploaded knowledge files (the S3 knowledge bucket once deployed).
UPLOAD_DIRECTORY = Path(__file__).resolve().parent.parent / ".data" / "uploads"
CHROMA_COLLECTION = "juniper_knowledge"

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
# Parallel tool calls must not open the Chroma client concurrently; Chroma's setup is not thread-safe.
_vector_store_lock = threading.Lock()


def prepare_query(question: str) -> str:
    return f"task: search result | query: {question}"


def prepare_document(content: str, title: str | None = None) -> str:
    return f"title: {title or 'none'} | text: {content}"


@cache
def get_vector_store() -> Chroma:
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    # OpenAIEmbeddings is LangChain's OpenAI-compatible client; OpenRouter routes
    # these requests to google/gemini-embedding-2.
    embeddings = OpenAIEmbeddings(
        model=EMBEDDING_MODEL,
        dimensions=EMBEDDING_DIMENSIONS,
        base_url=OPENROUTER_BASE_URL,
        api_key=OPENROUTER_API_KEY,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )
    return Chroma(
        collection_name=CHROMA_COLLECTION,
        embedding_function=embeddings,
        persist_directory=CHROMA_DIRECTORY,
    )


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
    status: str = "staged",
) -> int:
    path = Path(path)
    chunks = text_splitter.split_documents(load_pages(path))
    if not chunks:
        raise ValueError(f"No readable text found in {path.name}")
    ids = [f"{company_id}:{source_id}:{version}:{index}" for index in range(len(chunks))]
    metadatas = [
        {
            "company_id": company_id,
            "source_id": source_id,
            "version": version,
            "venue": venue,
            "status": status,
            "filename": path.name,
            "page": chunk.metadata["page"],
        }
        for chunk in chunks
    ]
    with _vector_store_lock:
        store = get_vector_store()
    store.add_texts(
        ids=ids,
        texts=[prepare_document(chunk.page_content, title or source_id) for chunk in chunks],
        metadatas=metadatas,
    )
    return len(chunks)


def set_version_status(company_id: str, source_id: str, version: str, status: str) -> int:
    """Set the status of every chunk of one document version; search only returns "published"."""
    with _vector_store_lock:
        store = get_vector_store()
    found = store.get(
        where={"$and": [{"company_id": company_id}, {"source_id": source_id}, {"version": version}]},
        include=["metadatas"],
    )
    if found["ids"]:
        # Metadata-only update on the Chroma collection, so chunks are not re-embedded.
        store._collection.update(ids=found["ids"], metadatas=[{**m, "status": status} for m in found["metadatas"]])
    return len(found["ids"])


def search_knowledge(query: str, company_id: str, k: int = 4) -> list[Document]:
    with _vector_store_lock:
        store = get_vector_store()
    return store.similarity_search(
        prepare_query(query),
        k=k,
        filter={"$and": [{"company_id": company_id}, {"status": "published"}]},
    )
