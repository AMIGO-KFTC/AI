"""search_documents 도구가 '질문과 관련 없다'고 판정된 검색 결과를 숨기고 모델에게 그 사실을 알리는지 확인한다."""

from amigo_agent import AgentConfig
from amigo_agent.engine.claude import ClaudeEngine
from amigo_agent.kb import EvidenceRegistry, KBAdapter

from test_claude_engine import FakeClient


class StubKB:
    def __init__(self, results):
        self.results = results

    def search(self, query, top_k=5, **_):
        return self.results


def hit(chunk_id, text, relevant, relevance=None):
    return {
        "chunk_id": chunk_id,
        "text": text,
        "score": 0.3,
        "citation": f"{chunk_id}.pdf p.1",
        "metadata": {"source_id": chunk_id, "file_name": f"{chunk_id}.pdf"},
        "relevance": relevance,
        "relevant": relevant,
    }


def run_tool(results, **config):
    engine = ClaudeEngine(AgentConfig(**config), client=FakeClient())
    registry = EvidenceRegistry()
    handler = engine._search_handler(KBAdapter(StubKB(results)), registry)
    return handler("search_documents", {"query": "퇴사자 계정 회수 절차"}), registry


def test_kb_adapter_carries_relevance_fields():
    [ev] = KBAdapter(StubKB([hit("a", "본문", True, 0.8)])).search("q")
    assert (ev.relevant, ev.relevance) == (True, 0.8)
    [ev] = KBAdapter(StubKB([hit("a", "본문", None)])).search("q")
    assert ev.relevant is None and ev.relevance is None


def test_irrelevant_results_are_dropped_when_some_are_relevant():
    out, registry = run_tool([hit("good", "정답 본문", True, 0.9), hit("bad", "엉뚱한 본문", False, 0.1)])
    assert "정답 본문" in out
    assert "엉뚱한 본문" not in out
    assert len(registry) == 1


def test_all_irrelevant_tells_the_model_there_is_no_answer_without_leaking_text():
    out, registry = run_tool([hit("bad1", "엉뚱한 본문 하나", False), hit("bad2", "엉뚱한 본문 둘", False)])
    assert "관련 있다고 판단되는 검색 결과가 없습니다" in out
    assert "bad1.pdf p.1" in out  # 가장 가까운 자료 이름은 알려 준다
    assert "엉뚱한 본문" not in out  # 본문은 근거로 잘못 쓰이지 않게 싣지 않는다
    assert len(registry) == 0  # 인용할 수 있는 E 번호도 만들지 않는다


def test_results_without_a_verdict_are_shown_as_before():
    out, registry = run_tool([hit("old", "판정 없는 본문", None)])
    assert "판정 없는 본문" in out and len(registry) == 1


def test_switch_off_keeps_old_behaviour():
    out, registry = run_tool([hit("bad", "엉뚱한 본문", False)], hide_irrelevant=False)
    assert "엉뚱한 본문" in out and len(registry) == 1
