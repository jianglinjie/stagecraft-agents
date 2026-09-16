"""Hybrid retrieval over the reference corpus: BM25 and embeddings, fused with RRF.

The planner gets back a pointer and a one-line summary per hit, never the section text.
Pointers go into a contract's ``sources``; whoever needs the text later resolves them. The
same rule as every other tool: results carry what the next decision needs, not the payload.

Two rankers, because they fail differently. BM25 matches the words a query uses ("PDF",
"captions", "datasheet") and misses paraphrase. Embeddings match meaning ("people new to
the topic" finds the beginners guide) and blur exact terms, ids and numbers. A document
either ranker finds is a candidate.

Fused with Reciprocal Rank Fusion, because the scores are not comparable. BM25 scores are
unbounded and depend on the corpus; cosine similarities sit in a narrow band that depends
on the embedding model. Normalising one against the other is tuning that breaks whenever
either side changes. RRF reads ranks only::

    score(d) = sum over rankers r that returned d of 1 / (k + rank_r(d)),  k = 60

Ties go to the document more rankers agreed on, then to the better single rank, then to
the pointer, so the order is deterministic.

The vector side is optional and says so. Without an embeddings endpoint (DeepSeek has none)
the index runs BM25 only, logs why once, and every result reports ``mode: bm25_only``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Literal, Protocol

import numpy as np
from openai import AsyncOpenAI
from pydantic import BaseModel, Field
from rank_bm25 import BM25Okapi

from stagecraft.config import EmbeddingConfig
from stagecraft.tools.registry import ToolSpec, tool
from stagecraft.tools.results import ToolResult

logger = logging.getLogger(__name__)

RRF_K = 60
SUMMARY_CHARS = 160
DEFAULT_CORPUS_DIR = "docs/corpus"

Mode = Literal["hybrid", "bm25_only", "empty"]

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have how i if in into is it its of on or
    our so that the their them then there these this to was we what when which who why will
    with you your""".split()
)


# -- corpus ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Chunk:
    """One ``##`` section of one document. ``pointer`` is ``<file stem>#<section slug>``."""

    pointer: str
    title: str
    text: str

    @property
    def summary(self) -> str:
        return summarise(self.text)


def chunk_markdown(doc_id: str, markdown: str) -> list[Chunk]:
    doc_title = doc_id
    heading: str | None = None
    lines: list[str] = []
    chunks: list[Chunk] = []

    def flush() -> None:
        body = " ".join(" ".join(lines).split())
        if body:
            section = heading or "overview"
            pointer = f"{doc_id}#{slug(section)}"
            chunks.append(Chunk(pointer=pointer, title=f"{doc_title}: {section}", text=body))
        lines.clear()

    for line in markdown.splitlines():
        if line.startswith("# "):
            doc_title = line[2:].strip()
        elif line.startswith("## "):
            flush()
            heading = line[3:].strip()
        else:
            lines.append(line)
    flush()
    return chunks


def load_corpus(directory: str | Path) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(Path(directory).glob("*.md")):
        chunks.extend(chunk_markdown(path.stem, path.read_text(encoding="utf-8")))
    return chunks


def summarise(text: str) -> str:
    """The first sentence, capped: the corpus opens every section with its topic sentence."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    if len(first) <= SUMMARY_CHARS:
        return first
    return first[: SUMMARY_CHARS - 1].rsplit(" ", 1)[0] + "…"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "section"


def tokenize(text: str) -> list[str]:
    return [token for token in re.findall(r"[a-z0-9]+", text.lower()) if token not in _STOPWORDS]


# -- fusion ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fused:
    key: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)


def reciprocal_rank_fusion(rankings: Mapping[str, Sequence[str]], *, k: int = RRF_K) -> list[Fused]:
    """Fuse ranked lists (best first) by summed ``1 / (k + rank)``, ranks starting at 1."""
    scores: dict[str, float] = {}
    ranks: dict[str, dict[str, int]] = {}
    for ranker, keys in rankings.items():
        for rank, key in enumerate(keys, start=1):
            if ranker in ranks.setdefault(key, {}):
                continue  # a ranker lists each key once; ignore repeats
            ranks[key][ranker] = rank
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    fused = [Fused(key=key, score=scores[key], ranks=ranks[key]) for key in scores]
    return sorted(fused, key=lambda f: (-f.score, -len(f.ranks), min(f.ranks.values()), f.key))


# -- embeddings --------------------------------------------------------------------------


class Embedder(Protocol):
    name: str

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class OpenAIEmbedder:
    """Any endpoint that serves ``POST /embeddings`` in the OpenAI format."""

    def __init__(self, config: EmbeddingConfig, *, batch_size: int = 64) -> None:
        self.name = config.model
        self.batch_size = batch_size
        self._config = config
        self._client: AsyncOpenAI | None = None

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        # Created on first use, so the client belongs to the event loop that serves requests.
        if self._client is None:
            self._client = AsyncOpenAI(base_url=self._config.base_url, api_key=self._config.api_key)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            response = await self._client.embeddings.create(model=self.name, input=batch)
            vectors.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
        return vectors


# -- index -------------------------------------------------------------------------------


class ReferenceHit(BaseModel):
    pointer: str
    title: str
    summary: str
    score: float
    matched_by: list[str]


@dataclass(frozen=True)
class IndexStats:
    chunks: int
    mode: Mode
    vector_error: str | None


class ReferenceIndex:
    """Chunks, a BM25 index and (when an embedder works) a matrix of unit vectors.

    Built by :meth:`load`, which is idempotent and runs on first search if nobody called it.
    Servers call it at startup, inside the event loop that will later serve searches.
    """

    def __init__(
        self,
        chunks: Sequence[Chunk] | None = None,
        *,
        corpus_dir: str | Path | None = None,
        embedder: Embedder | None = None,
        candidates: int = 20,
    ) -> None:
        self._chunks: list[Chunk] = list(chunks or [])
        self._corpus_dir = Path(corpus_dir) if corpus_dir is not None else None
        self.embedder = embedder
        self.candidates = candidates
        self._by_pointer: dict[str, int] = {}
        self._terms: list[frozenset[str]] = []
        self._bm25: BM25Okapi | None = None
        self._vectors: np.ndarray | None = None
        self.vector_error: str | None = None
        self._loaded = False
        self._lock = asyncio.Lock()

    @property
    def mode(self) -> Mode:
        if not self._chunks:
            return "empty"
        return "hybrid" if self._vectors is not None else "bm25_only"

    def stats(self) -> IndexStats:
        return IndexStats(chunks=len(self._chunks), mode=self.mode, vector_error=self.vector_error)

    async def load(self) -> IndexStats:
        async with self._lock:
            if self._loaded:
                return self.stats()
            if self._corpus_dir is not None:
                if self._corpus_dir.is_dir():
                    self._chunks.extend(load_corpus(self._corpus_dir))
                else:
                    logger.warning("reference corpus %s does not exist", self._corpus_dir)
            self._by_pointer = {chunk.pointer: i for i, chunk in enumerate(self._chunks)}
            if self._chunks:
                tokens = [tokenize(_indexed_text(chunk)) for chunk in self._chunks]
                self._terms = [frozenset(words) for words in tokens]
                self._bm25 = BM25Okapi(tokens)
                await self._embed_corpus()
            self._loaded = True
            return self.stats()

    async def has(self, pointer: str) -> bool:
        await self.load()
        return pointer in self._by_pointer

    async def search(self, query: str, top_k: int = 3) -> tuple[list[ReferenceHit], Mode]:
        await self.load()
        if not self._chunks:
            return [], "empty"
        rankings: dict[str, list[str]] = {"bm25": self._bm25_ranking(query)}
        mode: Mode = "bm25_only"
        vector_ranking = await self._vector_ranking(query)
        if vector_ranking is not None:
            rankings["vector"] = vector_ranking
            mode = "hybrid"
        hits: list[ReferenceHit] = []
        for fused in reciprocal_rank_fusion(rankings)[:top_k]:
            chunk = self._chunks[self._by_pointer[fused.key]]
            hits.append(
                ReferenceHit(
                    pointer=chunk.pointer,
                    title=chunk.title,
                    summary=chunk.summary,
                    score=round(fused.score, 5),
                    matched_by=list(fused.ranks),
                )
            )
        return hits, mode

    async def _embed_corpus(self) -> None:
        if self.embedder is None:
            return
        try:
            matrix = np.asarray(
                await self.embedder.embed([_indexed_text(c) for c in self._chunks]), dtype=float
            )
        except Exception as err:  # noqa: BLE001 - no vectors is a mode, not a crash
            self.vector_error = f"{type(err).__name__}: {err}"
            logger.warning(
                "embeddings unavailable (%s); reference search runs BM25 only", self.vector_error
            )
            return
        self._vectors = _unit_rows(matrix)

    def _bm25_ranking(self, query: str) -> list[str]:
        """Chunks sharing at least one query term, best BM25 score first.

        Candidacy is term overlap, not ``score > 0``: Okapi IDF is zero or negative for a term
        in half the chunks or more, which would drop real matches from a small corpus.
        """
        tokens = tokenize(query)
        if self._bm25 is None or not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        wanted = set(tokens)
        matching = [i for i, terms in enumerate(self._terms) if terms & wanted]
        matching.sort(key=lambda i: -scores[i])  # stable: ties keep corpus order
        return [self._chunks[i].pointer for i in matching[: self.candidates]]

    async def _vector_ranking(self, query: str) -> list[str] | None:
        if self._vectors is None or self.embedder is None:
            return None
        try:
            (vector,) = await self.embedder.embed([query])
        except Exception as err:  # noqa: BLE001 - one failed query degrades that query only
            logger.warning("query embedding failed (%s); using BM25 for this search", err)
            return None
        query_vector = _unit_rows(np.asarray([vector], dtype=float))[0]
        similarities = self._vectors @ query_vector
        order = np.argsort(-similarities, kind="stable")
        return [self._chunks[i].pointer for i in order[: self.candidates]]


def reference_index_from_env(env: Mapping[str, str] | None = None) -> ReferenceIndex:
    """The corpus in ``STAGECRAFT_CORPUS_DIR`` (default ``docs/corpus``), with embeddings from
    ``EMBEDDING_*`` or the chat endpoint's settings. Not loaded yet: call :meth:`load`."""
    source = os.environ if env is None else env
    config = EmbeddingConfig.from_env(source)
    return ReferenceIndex(
        corpus_dir=source.get("STAGECRAFT_CORPUS_DIR", DEFAULT_CORPUS_DIR),
        embedder=OpenAIEmbedder(config) if config is not None else None,
    )


# -- tool --------------------------------------------------------------------------------


class ReferenceSearch(ToolResult):
    query: str
    mode: Mode
    hits: list[ReferenceHit]
    next_step: str


def build_retrieval_tools(index: ReferenceIndex) -> list[ToolSpec]:
    @tool
    async def search_references(
        query: Annotated[
            str, "What the stage needs guidance on: audience, tone, format or structure."
        ],
        top_k: Annotated[int, Field(ge=1, le=8, description="How many pointers to return.")] = 3,
    ) -> ReferenceSearch:
        """Search the style, audience and format guides. Returns pointers and one-line summaries.

        Put the pointers a stage relies on into that stage's sources. The guides' full text is
        never returned here.
        """
        hits, mode = await index.search(query, top_k)
        if hits:
            step = "Put the pointers this stage relies on into sources when you write its contract."
        else:
            step = "Nothing matched. Try other words, or write the contract without sources."
        return ReferenceSearch(query=query, mode=mode, hits=hits, next_step=step)

    return [search_references]


def _indexed_text(chunk: Chunk) -> str:
    return f"{chunk.title}. {chunk.text}"


def _unit_rows(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    return matrix / np.where(norms == 0, 1.0, norms)
