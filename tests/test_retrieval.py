"""Hybrid retrieval: fusion order, pointer-only results, degradation, and cited sources."""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

from stagecraft.agents.fake_model import FakeModel, reply, submit, tool_call
from stagecraft.agents.orchestrator import run_orchestrator_turn
from stagecraft.agents.planner import PlannerOutput
from stagecraft.agents.roles import ROLE_TOOLS
from stagecraft.agents.runtime import build_runtime
from stagecraft.config import EmbeddingConfig
from stagecraft.plan import PlanStore
from stagecraft.tools import RunContext
from stagecraft.tools.fake import FakeWorkspace
from stagecraft.tools.plan import build_plan_tools
from stagecraft.tools.retrieval import (
    Chunk,
    ReferenceIndex,
    build_retrieval_tools,
    chunk_markdown,
    reciprocal_rank_fusion,
    summarise,
)

CORPUS = Path(__file__).resolve().parents[1] / "docs" / "corpus"


class KeywordEmbedder:
    """A fake embedding model: one axis per keyword group, valued by how often it appears."""

    name = "keywords"

    def __init__(self, axes: Sequence[tuple[str, ...]], *, fail: bool = False) -> None:
        self.axes = axes
        self.fail = fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("Error code: 404 - page not found")
        self.calls.append(list(texts))
        return [
            [float(sum(text.lower().count(word) for word in axis)) for axis in self.axes]
            for text in texts
        ]


def write(directory: Path, name: str, text: str) -> None:
    (directory / name).write_text(text, encoding="utf-8")


# -- fusion -------------------------------------------------------------------------------


def test_rrf_sums_reciprocal_ranks_so_agreement_beats_a_single_first_place() -> None:
    fused = reciprocal_rank_fusion({"bm25": ["a", "b", "c"], "vector": ["c", "a", "d"]})

    assert [f.key for f in fused] == ["a", "c", "b", "d"]
    assert fused[0].score == pytest.approx(1 / 61 + 1 / 62)
    assert fused[1].score == pytest.approx(1 / 63 + 1 / 61)
    assert fused[2].score == pytest.approx(1 / 62)
    assert fused[0].ranks == {"bm25": 1, "vector": 2}

    # q is second in both lists; p and r are first in one each.
    fused = reciprocal_rank_fusion({"bm25": ["p", "q"], "vector": ["r", "q"]})
    assert [f.key for f in fused] == ["q", "p", "r"]


def test_rrf_k_decides_how_much_a_first_place_counts_and_ties_break_deterministically() -> None:
    rankings = {"bm25": ["a", "b"], "vector": ["c", "d", "b"]}

    assert [f.key for f in reciprocal_rank_fusion(rankings)][0] == "b"
    assert [f.key for f in reciprocal_rank_fusion(rankings, k=0)][:2] == ["a", "c"]

    tied = reciprocal_rank_fusion({"bm25": ["y", "x"], "vector": ["x", "y"]})
    assert [f.key for f in tied] == ["x", "y"]  # same score, same rankers, same best rank
    assert [f.key for f in reciprocal_rank_fusion({"bm25": ["z"], "vector": ["z", "w"]})] == [
        "z",
        "w",
    ]


# -- corpus and index ---------------------------------------------------------------------


def test_documents_split_into_sections_with_stable_pointers_and_one_sentence_summaries() -> None:
    chunks = chunk_markdown(
        "tone-playful",
        "# Playful tone\n\nAn intro.\n\n## Voice rules\nFirst sentence. Second one.\n\n"
        "## What to avoid\nAvoid sarcasm!\n",
    )

    assert [c.pointer for c in chunks] == [
        "tone-playful#overview",
        "tone-playful#voice-rules",
        "tone-playful#what-to-avoid",
    ]
    assert chunks[1].title == "Playful tone: Voice rules"
    assert chunks[1].summary == "First sentence."
    capped = summarise("word " * 100)
    assert len(capped) <= 160 and capped.endswith("…")


async def test_search_returns_pointers_and_summaries_never_the_section_text(
    tmp_path: Path,
) -> None:
    secret = "The embargoed launch date is the ninth."
    write(
        tmp_path,
        "launch.md",
        f"# Launch\n\n## Timing\nLaunch timing follows the review cycle. {secret} "
        + "More detail follows here. " * 40,
    )
    write(tmp_path, "other.md", "# Other\n\n## Notes\nUnrelated notes about captions.")
    (search,) = build_retrieval_tools(ReferenceIndex(corpus_dir=tmp_path))

    result = await search.invoke({"query": "launch timing review", "top_k": 5})

    data = json.loads(result.model_dump_json())
    assert data["status"] == "ok" and data["mode"] == "bm25_only"
    assert [hit["pointer"] for hit in data["hits"]] == ["launch#timing"]
    assert data["hits"][0]["summary"] == "Launch timing follows the review cycle."
    assert set(data["hits"][0]) == {"pointer", "title", "summary", "score", "matched_by"}
    assert "embargoed" not in json.dumps(data) and "More detail" not in json.dumps(data)


async def test_vectors_find_what_bm25_misses_and_fusion_puts_agreement_first(
    tmp_path: Path,
) -> None:
    write(
        tmp_path, "pdf.md", "# PDF\n\n## Tables\nPut specifications in one table, units in headers."
    )
    write(tmp_path, "beginners.md", "# Beginners\n\n## Terms\nDefine every term for a beginner.")
    write(tmp_path, "video.md", "# Video\n\n## Captions\nBurn in captions for silent viewing.")
    embedder = KeywordEmbedder([("newcomer", "beginner"), ("table", "units"), ("caption",)])
    index = ReferenceIndex(corpus_dir=tmp_path, embedder=embedder)

    hits, mode = await index.search("units table for newcomers", top_k=2)

    assert mode == "hybrid"
    # BM25 has no word in common with the beginners guide; the vector side finds it.
    assert [(hit.pointer, hit.matched_by) for hit in hits] == [
        ("pdf#tables", ["bm25", "vector"]),
        ("beginners#terms", ["vector"]),
    ]
    assert len(embedder.calls) == 2  # the corpus once at load, then the query


async def test_without_embeddings_search_runs_bm25_only_and_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    write(
        tmp_path, "pdf.md", "# PDF\n\n## Tables\nPut specifications in one table, units in headers."
    )
    index = ReferenceIndex(corpus_dir=tmp_path, embedder=KeywordEmbedder([("table",)], fail=True))

    with caplog.at_level(logging.WARNING):
        stats = await index.load()
    hits, mode = await index.search("table units")

    assert stats.mode == "bm25_only" and "404" in (stats.vector_error or "")
    assert "BM25 only" in caplog.text
    assert mode == "bm25_only" and hits[0].matched_by == ["bm25"]
    empty = ReferenceIndex(corpus_dir=tmp_path / "missing")
    assert await empty.search("anything") == ([], "empty")


async def test_the_shipped_corpus_answers_format_and_audience_questions() -> None:
    index = ReferenceIndex(corpus_dir=CORPUS)
    stats = await index.load()

    assert stats.chunks >= 40
    hits, _ = await index.search("PDF datasheet specification table")
    assert hits[0].pointer.startswith("format-pdf-datasheet#")
    hits, _ = await index.search("playful headlines")
    assert hits[0].pointer == "tone-playful#headlines"


def test_embedding_settings_fall_back_to_the_chat_endpoint_and_can_be_switched_off() -> None:
    env = {"OPENAI_BASE_URL": "https://chat.example/v1", "OPENAI_API_KEY": "k1"}

    assert EmbeddingConfig.from_env(env) == EmbeddingConfig(
        base_url="https://chat.example/v1", api_key="k1", model="text-embedding-3-small"
    )
    own = EmbeddingConfig.from_env(
        {
            **env,
            "EMBEDDING_BASE_URL": "https://emb.example/v1",
            "EMBEDDING_API_KEY": "k2",
            "EMBEDDING_MODEL": "e5",
        }
    )
    assert own is not None and (own.base_url, own.api_key, own.model) == (
        "https://emb.example/v1",
        "k2",
        "e5",
    )
    assert EmbeddingConfig.from_env({**env, "EMBEDDING_MODEL": "none"}) is None
    assert EmbeddingConfig.from_env({}) is None


# -- contracts ----------------------------------------------------------------------------


async def test_contracts_cite_pointers_from_the_index_and_invented_ones_are_refused() -> None:
    store = PlanStore()
    store.create_plan(chat_id="c", objective="o", role="orchestrator")
    index = ReferenceIndex([Chunk(pointer="tone-playful#headlines", title="t", text="Short.")])
    tools = {spec.name: spec for spec in build_plan_tools(store, references=index)}
    ctx = RunContext(role="planner", chat_id="c")
    write_contract = tools["plan_write_stage_contract"]

    written = await write_contract.invoke(
        {
            "plan_id": "plan_0001",
            "goal": "g",
            "work_items": [],
            "sources": ["tone-playful#headlines"],
        },
        ctx,
    )
    refused = await write_contract.invoke(
        {"plan_id": "plan_0001", "goal": "g", "work_items": [], "sources": ["tone-playful#jokes"]},
        ctx,
    )

    assert written.status == "ok"
    (stage,) = store.get_plan("plan_0001").stages
    assert stage.contract is not None and stage.contract.sources == ["tone-playful#headlines"]
    assert refused.status == "error" and refused.code == "not_found"  # type: ignore[attr-defined]
    assert "tone-playful#jokes" in refused.message  # type: ignore[attr-defined]
    assert "search_references" in (refused.hint or "")  # type: ignore[attr-defined]


async def test_sources_are_refused_when_no_corpus_is_configured() -> None:
    store = PlanStore()
    store.create_plan(chat_id="c", objective="o", role="orchestrator")
    (write_contract,) = [
        s for s in build_plan_tools(store) if s.name == "plan_write_stage_contract"
    ]

    refused = await write_contract.invoke(
        {"plan_id": "plan_0001", "goal": "g", "work_items": [], "sources": ["x#y"]},
        RunContext(role="planner", chat_id="c"),
    )

    assert refused.status == "error" and refused.code == "not_found"  # type: ignore[attr-defined]


async def test_the_planner_searches_then_cites_what_it_found() -> None:
    scripts: dict[str, list[Any]] = {
        "orchestrator": [
            tool_call("plan_create", objective="playful series"),
            tool_call("dispatch_planner", plan_id="plan_0001", goal="playful series"),
            reply("planned"),
        ],
        "planner": [
            tool_call("search_references", query="playful headlines"),
            tool_call(
                "plan_write_stage_contract",
                plan_id="plan_0001",
                goal="Write playful headlines",
                work_items=[{"id": "w1", "name": "draft", "instruction": "playful draft"}],
                sources=["tone-playful#headlines"],
            ),
            submit("submit_plan", PlannerOutput(summary="one", questions=[], done_authoring=True)),
        ],
    }
    models = {role: FakeModel(scripts.get(role, [])) for role in ROLE_TOOLS}
    runtime = build_runtime(
        chat_id="chat_1",
        models=models,  # type: ignore[arg-type]
        workspace=FakeWorkspace(render_delay_seconds=0),
        references=ReferenceIndex(corpus_dir=CORPUS),
    )

    await run_orchestrator_turn(runtime, "A playful series, plan first.")

    planner = models["planner"]
    assert "search_references" in planner.calls[0].tool_names
    instructions = planner.calls[0].system_instructions or ""
    assert "search_references" in instructions and "sources" in instructions
    found = planner.calls[1].tool_outputs()[-1]
    assert found["hits"][0]["pointer"] == "tone-playful#headlines"
    (stage,) = runtime.store.get_plan("plan_0001").stages
    assert stage.contract is not None and stage.contract.sources == ["tone-playful#headlines"]
    assert "search_references" not in ROLE_TOOLS["executor"]
