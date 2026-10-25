"""Claude 엔진 경로를 가짜 Anthropic 클라이언트로 검증한다(네트워크·API 키 불필요).

실제 SDK 응답처럼 content 블록(thinking/text/tool_use)과 stop_reason 을 돌려주고,
요청 파라미터(모델, effort, 구조화 출력, fallbacks, strict 도구)가 의도대로 나가는지 확인한다.
"""

import json
import re
from types import SimpleNamespace

from amigo_agent import AgentConfig, HandoverAgent
from amigo_agent.prompts import ANALYSIS_SYSTEM, AUTO_FILL_SYSTEM, INTERPRET_SYSTEM, PLAN_SYSTEM, SUMMARY_SYSTEM
from amigo_agent.template import SLOT_BY_KEY, SLOTS

from conftest import PROFILE


def text_block(payload):
    return SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))


def tool_use(query, uid="toolu_1"):
    return SimpleNamespace(type="tool_use", id=uid, name="search_documents", input={"query": query})


USAGE = SimpleNamespace(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=1000)


def response(blocks, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="", signature="sig")] + blocks, stop_reason=stop, usage=USAGE)


class FakeMessages:
    def __init__(self):
        self.calls = []

    def create(self, **kwargs):
        kwargs = {**kwargs, "messages": list(kwargs["messages"])}  # 이후 루프에서 리스트가 늘어나므로 호출 시점 스냅샷
        self.calls.append(kwargs)
        system = kwargs["system"][0]["text"]
        messages = kwargs["messages"]
        last = messages[-1]["content"]
        if system == ANALYSIS_SYSTEM:
            title = re.search(r"## 작성할 장: (.+)", messages[0]["content"]).group(1).strip()
            spec = next(s for s in SLOTS if s.title == title)
            if spec.key == "stakeholders" and isinstance(last, str):
                return response([tool_use("재무팀 담당자 연락처")], stop="tool_use")
            return response([text_block(analysis_payload(spec, messages))])
        if system == AUTO_FILL_SYSTEM:
            ids = re.findall(r"target_id=(t\d+)", messages[0]["content"])
            return response([text_block({"values": [{"target_id": i, "value": "수집된 내용 기준 정상 운영 중임."} for i in ids]})])
        if system == PLAN_SYSTEM:
            return response([text_block({"duplicates": []})])
        if system == SUMMARY_SYSTEM:
            return response([text_block({"message": "자료 5건에서 홈페이지 운영 업무를 파악했어요."})])
        if system == INTERPRET_SYSTEM:
            user = messages[0]["content"]
            if "새 메시지\n네" in user:
                return response([text_block({"intent": "confirm", "updates": [], "resolved_gap_ids": [], "reply": "확인했습니다."})])
            gap_id = re.search(r"- id: (gap-\w+)", user).group(1)
            item_id = re.search(r"id=(regular-\w+) \| 정기업무 항목 1", user).group(1)
            return response(
                [
                    text_block(
                        {
                            "intent": "answer",
                            "updates": [
                                {"slot": "regular", "item_id": item_id, "title": "정기업무 항목 1", "fields": [{"key": "decisions", "value": "주요 의사결정 및 판단 근거 값 1. 판단 근거: 예산 절감을 위해 결정함."}], "evidence_ids": []}
                            ],
                            "resolved_gap_ids": [gap_id],
                            "reply": "판단 근거를 '예산 절감'으로 정리했어요. 맞으면 '네', 고칠 부분이 있으면 말씀해 주세요.",
                        }
                    )
                ]
            )
        raise AssertionError("unexpected call")


def analysis_payload(spec, messages):
    fields = {f.key: "" for f in spec.fields}
    first = dict(fields)
    if spec.key == "stakeholders":
        first.update(org="재무팀", name="재무팀", role="대금 지급 협의", note="", scope="내부", project="")
        evidence = ["E1"] if "E1" in json.dumps([m["content"] for m in messages], ensure_ascii=False, default=str) else []
        items = [
            {"title": "재무팀", "fields": first, "evidence_ids": evidence + ["E99"]},
            {"title": "근거 없는 업체", "fields": dict(fields, name="근거 없는 업체"), "evidence_ids": ["E99"]},
        ]
        gaps = [{"description": "재무팀 관련 프로젝트 없음", "question": "재무팀과는 어느 프로젝트에서 협의하셨나요?", "priority": "high", "related_item_title": "재무팀", "field": "project", "element": "", "ask_for_document": False}]
        return {"items": items, "coverage": "partial", "summary": "재무팀과 협업", "gaps": gaps}
    if spec.key == "regular":
        values = {f.key: f"{f.label} 값 1" for f in spec.fields}
        items = [{"title": "정기업무 항목 1", "fields": values, "evidence_ids": ["E1"]}]
        gaps = [
            # 칸에 값은 있지만 빠진 세부사항(판단 근거)만 묻는 질문은 남긴다(1질문 1암묵지)
            {"description": "결정 내용은 있으나 판단 근거 없음", "question": "결정 값 1을 그렇게 정한 이유는 무엇인가요?", "priority": "high", "related_item_title": "정기업무 항목 1", "field": "decisions", "element": "결정 당시의 상황과 판단 근거", "ask_for_document": False},
            # 묻지 않는 칸(업무 개요 = 미질의), 이미 채운 칸을 통째로 묻는 질문, 칸과 무관한 궁금증은 코드가 버린다
            {"description": "개요", "question": "이 업무의 목적은 무엇인가요?", "priority": "high", "related_item_title": "정기업무 항목 1", "field": "overview", "element": "목적", "ask_for_document": False},
            {"description": "절차", "question": "절차를 알려 주세요", "priority": "high", "related_item_title": "정기업무 항목 1", "field": "procedure", "element": "", "ask_for_document": False},
            {"description": "기타", "question": "입찰 공고는 언제 하나요?", "priority": "high", "related_item_title": "", "field": "", "element": "", "ask_for_document": False},
        ]
        return {"items": items, "coverage": "partial", "summary": "정기업무 확인", "gaps": gaps}
    items = []
    for n in range(spec.min_items):
        values = {f.key: f"{f.label} 값 {n + 1}" for f in spec.fields}
        items.append({"title": f"{spec.title} 항목 {n + 1}", "fields": values, "evidence_ids": ["E1"]})
    return {"items": items, "coverage": "sufficient", "summary": f"{spec.title} 확인", "gaps": []}


class FakeClient:
    def __init__(self):
        self.messages = FakeMessages()
        self.beta = SimpleNamespace(messages=self.messages)


def test_claude_engine_end_to_end(kb):
    client = FakeClient()
    agent = HandoverAgent(AgentConfig(llm_mode="claude", model="claude-opus-5-5", max_concurrency=1), client=client)
    assert agent.describe() == {"engine": "claude", "model": "claude-opus-5-5"}

    seen = []
    result = agent.start("c1", PROFILE, kb, on_event=seen.append)
    snap = result.snapshot
    usage_events = [e for e in seen if e["type"] == "usage"]
    assert usage_events and all(e["output_tokens"] == 50 for e in usage_events)
    assert usage_events[0]["cost_usd"] == round((100 * 4 + 50 * 20 + 1000 * 0.2) / 1e6, 6)  # Opus 5.5 단가
    assert agent.usage()["requests"] == len(usage_events)
    assert [m["kind"] for m in result.messages] == ["summary", "question"]
    assert result.messages[0]["content"].startswith("자료 5건에서")
    assert "결정 값 1을 그렇게 정한 이유는 무엇인가요?" in result.messages[1]["content"]  # 양식 순서: 첫 세부업무의 의사결정(빠진 판단 근거)
    questions = [g["question"] for g in snap["gaps"]]
    assert not {"이 업무의 목적은 무엇인가요?", "절차를 알려 주세요", "입찰 공고는 언제 하나요?", "재무팀과는 어느 프로젝트에서 협의하셨나요?"} & set(questions)
    assert next(g for g in snap["gaps"] if g["question"].startswith("결정 값 1"))["element"] == "결정 당시의 상황과 판단 근거"
    assert [g["field"] for g in snap["gaps"] if g["slot"] == "regular"] == ["decisions"]  # 나머지 칸은 자료로 채워 묻지 않는다

    contacts = snap["slots"]["stakeholders"]
    assert [it["title"] for it in contacts["items"]] == ["재무팀"]  # 근거 없는 항목은 버린다
    assert contacts["items"][0]["citations"][0]["label"].startswith("업무정의서.pdf")
    assert snap["slots"]["overview"]["coverage"] == "sufficient"

    # 도구 호출(Function Calling) 루프: tool_use → 검색 결과 tool_result → 최종 JSON
    analysis_calls = [c for c in client.messages.calls if c["system"][0]["text"] == ANALYSIS_SYSTEM]
    followups = [c for c in analysis_calls if isinstance(c["messages"][-1]["content"], list)]
    assert len(followups) == 1
    tool_result = followups[0]["messages"][-1]["content"][0]
    assert tool_result["type"] == "tool_result" and tool_result["tool_use_id"] == "toolu_1"
    assert "[E" in tool_result["content"] and "재무팀" in tool_result["content"]
    assert followups[0]["messages"][1]["content"][0].type == "thinking"  # thinking 블록을 그대로 되돌려 보낸다

    call = analysis_calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["output_config"]["effort"] == "medium"
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["betas"] == ["server-side-fallback-2026-07-01"] and call["fallbacks"] == "default"
    assert "thinking" not in call and "tool_choice" not in call
    assert call["tools"][0]["name"] == "search_documents" and call["tools"][0]["strict"] is True

    # 답변 해석 → 교차 확인 → 확인
    result = agent.send_message("c1", "예산을 아끼려고 그렇게 정했어요", kb)
    assert result.messages[0]["kind"] == "confirm"
    item = result.snapshot["slots"]["regular"]["items"][0]
    assert item["fields"]["decisions"].startswith("주요 의사결정 및 판단 근거 값 1. 판단 근거")  # 기존 값 뒤에 이어 붙인다
    assert any(c["label"].startswith("인계자 답변") for c in item["citations"])
    interpret_call = next(c for c in client.messages.calls if c["system"][0]["text"] == INTERPRET_SYSTEM)
    assert interpret_call["output_config"]["effort"] == "low"
    assert "- 묻는 세부사항: 결정 당시의 상황과 판단 근거" in interpret_call["messages"][0]["content"]

    result = agent.send_message("c1", "네", kb)
    assert result.messages[-1]["meta"]["choice"] == "yesno"  # 다음은 협업 유의사항(예/아니요)
    result = agent.finish("c1", kb)
    assert result.snapshot["stage"] == "review"
    doc = result.snapshot["document"]
    assert "## 1. 업무 개요" not in doc and "인수자 핵심 당부사항" not in doc
    # 문서는 코드가 그리고, 모델은 미질의 칸(진행상황 요약 등, 수집된 항목만 근거)에만 쓴다
    assert {c["system"][0]["text"] for c in client.messages.calls} <= {ANALYSIS_SYSTEM, INTERPRET_SYSTEM, PLAN_SYSTEM, SUMMARY_SYSTEM, AUTO_FILL_SYSTEM}
    assert "수집된 내용 기준 정상 운영 중임.</td>" in doc  # 업무 단위 요약의 진행상황 요약 칸
    assert "확인 필요 사항" not in doc and "근거 자료" not in doc  # 양식에 없는 장은 싣지 않는다


def test_slot_output_models_are_strict():
    from anthropic import transform_schema

    from amigo_agent.engine.claude import InterpretOutput, slot_output_model

    for spec in SLOTS:
        schema = transform_schema(slot_output_model(spec.key))
        item = schema["$defs"][f"{spec.key.title()}Item"]
        assert item["additionalProperties"] is False
        fields = schema["$defs"][f"{spec.key.title()}Fields"]
        assert set(fields["required"]) == {f.key for f in SLOT_BY_KEY[spec.key].fields}
    schema = transform_schema(InterpretOutput)
    assert schema["additionalProperties"] is False


def test_question_plan_only_drops_duplicates_and_keeps_the_given_order(kb):
    from amigo_agent.engine.claude import ClaudeEngine
    from amigo_agent.prompts import PLAN_SYSTEM
    class PlanMessages:
        def create(self, **kwargs):
            assert kwargs["system"][0]["text"] == PLAN_SYSTEM
            assert "gap-b" in kwargs["messages"][0]["content"]
            plan = {"duplicates": [{"drop_id": "gap-c", "keep_id": "gap-b"}, {"drop_id": "gap-x", "keep_id": "gap-a"}]}
            return response([text_block(plan)])

    engine = ClaudeEngine(AgentConfig(llm_mode="claude"), client=SimpleNamespace(beta=SimpleNamespace(messages=PlanMessages())))
    gaps = [
        {"id": "gap-a", "slot": "stakeholders", "question": "업체 담당자는 누구인가요?", "priority": 1, "status": "open"},
        {"id": "gap-b", "slot": "irregular", "question": "입찰 공고 일정은 언제인가요?", "priority": 2, "status": "open"},
        {"id": "gap-c", "slot": "regular", "question": "공고 일정이 조정됐나요?", "priority": 1, "status": "open"},
    ]
    order, drops = engine.plan_questions(PROFILE, gaps)
    assert order == ["gap-a", "gap-b"] and drops == {"gap-c"}  # 모르는 id 는 무시하고, 남은 질문은 들어온 순서 그대로(순서는 코드가 장 순서로 정한다)


def test_haiku_requests_skip_unsupported_parameters(kb):
    """Haiku 4.5 에는 effort·fallbacks 를 보내지 않는다(보내면 400)."""
    client = FakeClient()
    config = AgentConfig(llm_mode="claude", model="claude-haiku-4-5", max_concurrency=1)
    HandoverAgent(config, client=client).start("h1", PROFILE, kb)
    call = client.messages.calls[0]
    assert call["model"] == "claude-haiku-4-5"
    assert "effort" not in call["output_config"] and call["output_config"]["format"]["type"] == "json_schema"
    assert "fallbacks" not in call and "betas" not in call


def test_default_model_is_sonnet_and_sends_effort(kb, monkeypatch):
    monkeypatch.delenv("AMIGO_LLM_MODEL", raising=False)
    client = FakeClient()
    config = AgentConfig(llm_mode="claude", max_concurrency=1)
    assert config.model == "claude-sonnet-5-5"
    HandoverAgent(config, client=client).start("s1", PROFILE, kb)
    call = client.messages.calls[0]
    assert call["model"] == "claude-sonnet-5-5"
    assert call["output_config"].get("effort") == "medium"


def test_analysis_prompt_asks_for_reference_documents():
    """참고자료 필드는 자료에서 찾은 문서명으로 채우되, 없으면 짐작하지 않고 비운다고 안내한다."""
    assert "'references'(기타(참고자료 등))" in ANALYSIS_SYSTEM
    assert "짐작해서 쓰지 말고 빈 문자열" in ANALYSIS_SYSTEM


def test_reference_field_value_reaches_document(kb):
    agent = HandoverAgent(AgentConfig(llm_mode="claude", model="claude-opus-5-5", max_concurrency=1), client=FakeClient())
    agent.start("c2", PROFILE, kb)
    doc = agent.finish("c2", kb).snapshot["document"]
    assert '<th>기타(참고자료 등)</th><td colspan="5">기타(참고자료 등) 값 1</td>' in doc  # 정기업무 카드는 협업관계 표(5열)를 품는 HTML 표
