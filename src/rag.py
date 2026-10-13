from functools import cache
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter
from pypdf import PdfReader

from . import config

text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)


def prepare_query(question: str) -> str:
    return f"task: search result | query: {question}"


def prepare_document(content: str, title: str | None = None) -> str:
    return f"title: {title or 'none'} | text: {content}"


@cache
def get_vector_store() -> Chroma:
    if not config.OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    # OpenAIEmbeddings is LangChain's OpenAI-compatible client; OpenRouter routes
    # these requests to google/gemini-embedding-2.
    embeddings = OpenAIEmbeddings(
        model=config.EMBEDDING_MODEL,
        dimensions=config.EMBEDDING_DIMENSIONS,
        base_url=config.OPENROUTER_BASE_URL,
        api_key=config.OPENROUTER_API_KEY,
        check_embedding_ctx_length=False,
        model_kwargs={"encoding_format": "float"},
    )
    return Chroma(
        collection_name=config.CHROMA_COLLECTION,
        embedding_function=embeddings,
        persist_directory=config.CHROMA_DIRECTORY,
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
    get_vector_store().add_texts(
        ids=ids,
        texts=[prepare_document(chunk.page_content, title or source_id) for chunk in chunks],
        metadatas=metadatas,
    )
    return len(chunks)


def search_knowledge(query: str, company_id: str, k: int = 4) -> list[Document]:
    return get_vector_store().similarity_search(
        prepare_query(query),
        k=k,
        filter={"$and": [{"company_id": company_id}, {"status": "published"}]},
    )
