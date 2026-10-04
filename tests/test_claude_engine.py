"""Claude 엔진 경로를 가짜 Anthropic 클라이언트로 검증한다(네트워크·API 키 불필요).

실제 SDK 응답처럼 content 블록(thinking/text/tool_use)과 stop_reason 을 돌려주고,
요청 파라미터(모델, effort, 구조화 출력, fallbacks, strict 도구)가 의도대로 나가는지 확인한다.
"""

import json
import re
from types import SimpleNamespace

from amigo_agent import AgentConfig, HandoverAgent
from amigo_agent.prompts import ANALYSIS_SYSTEM, COMPOSE_SYSTEM, INTERPRET_SYSTEM, SUMMARY_SYSTEM
from amigo_agent.template import SLOT_BY_KEY, SLOTS

from conftest import PROFILE


def text_block(payload):
    return SimpleNamespace(type="text", text=json.dumps(payload, ensure_ascii=False))


def tool_use(query, uid="toolu_1"):
    return SimpleNamespace(type="tool_use", id=uid, name="search_documents", input={"query": query})


def response(blocks, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="", signature="sig")] + blocks, stop_reason=stop)


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
            if spec.key == "contacts" and isinstance(last, str):
                return response([tool_use("재무팀 담당자 연락처")], stop="tool_use")
            return response([text_block(analysis_payload(spec, messages))])
        if system == SUMMARY_SYSTEM:
            return response([text_block({"message": "자료 5건에서 홈페이지 운영 업무를 파악했어요."})])
        if system == INTERPRET_SYSTEM:
            user = messages[0]["content"]
            if "새 메시지\n네" in user:
                return response([text_block({"intent": "confirm", "updates": [], "resolved_gap_ids": [], "reply": "확인했습니다."})])
            gap_id = re.search(r"- id: (gap-\w+)", user).group(1)
            item_id = re.search(r"id=(contacts-\w+) \| 재무팀", user).group(1)
            return response(
                [
                    text_block(
                        {
                            "intent": "answer",
                            "updates": [
                                {"slot": "contacts", "item_id": item_id, "title": "재무팀", "fields": [{"key": "contact", "value": "내선 2345"}], "evidence_ids": []}
                            ],
                            "resolved_gap_ids": [gap_id],
                            "reply": "재무팀 박지훈 차장의 연락처를 내선 2345 로 정리했어요. 맞으면 '네', 고칠 부분이 있으면 말씀해 주세요.",
                        }
                    )
                ]
            )
        if system == COMPOSE_SYSTEM:
            return response([text_block({"overview": "홈페이지 운영 전반을 인계합니다.", "key_points": ["11월 웹 접근성 인증 갱신", "리뉴얼 예산 승인 확인"]})])
        raise AssertionError("unexpected call")


def analysis_payload(spec, messages):
    fields = {f.key: "" for f in spec.fields}
    first = dict(fields)
    if spec.key == "contacts":
        first.update(party="재무팀", person="박지훈 차장", role="대금 지급 협의")
        evidence = ["E1"] if "E1" in json.dumps([m["content"] for m in messages], ensure_ascii=False, default=str) else []
        items = [
            {"title": "재무팀", "fields": first, "evidence_ids": evidence + ["E99"]},
            {"title": "근거 없는 업체", "fields": dict(fields, party="근거 없는 업체"), "evidence_ids": ["E99"]},
        ]
        gaps = [{"description": "재무팀 연락처 없음", "question": "재무팀 박지훈 차장의 연락처를 알려 주시겠어요?", "priority": "high", "related_item_title": "재무팀", "ask_for_document": False}]
        return {"items": items, "coverage": "partial", "summary": "재무팀과 협업", "gaps": gaps}
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
    agent = HandoverAgent(AgentConfig(llm_mode="claude", max_concurrency=1), client=client)
    assert agent.describe() == {"engine": "claude", "model": "claude-opus-5-5"}

    result = agent.start("c1", PROFILE, kb)
    snap = result.snapshot
    assert [m["kind"] for m in result.messages] == ["summary", "question"]
    assert result.messages[0]["content"].startswith("자료 5건에서")
    assert "재무팀 박지훈 차장의 연락처" in result.messages[1]["content"]

    contacts = snap["slots"]["contacts"]
    assert [it["title"] for it in contacts["items"]] == ["재무팀"]  # 근거 없는 항목은 버린다
    assert contacts["items"][0]["citations"][0]["label"].startswith("업무정의서.pdf")
    assert contacts["coverage"] == "partial"
    assert snap["slots"]["duties"]["coverage"] == "sufficient"

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
    result = agent.send_message("c1", "재무팀은 내선 2345 로 연락하면 됩니다", kb)
    assert result.messages[0]["kind"] == "confirm"
    item = result.snapshot["slots"]["contacts"]["items"][0]
    assert item["fields"]["contact"] == "내선 2345"
    assert any(c["label"].startswith("인계자 답변") for c in item["citations"])
    interpret_call = next(c for c in client.messages.calls if c["system"][0]["text"] == INTERPRET_SYSTEM)
    assert interpret_call["output_config"]["effort"] == "low"

    result = agent.send_message("c1", "네", kb)
    assert result.snapshot["stage"] == "review"  # 남은 질문이 없으면 바로 문서 생성
    doc = result.snapshot["document"]
    assert "홈페이지 운영 전반을 인계합니다." in doc
    assert "1. 11월 웹 접근성 인증 갱신" in doc
    assert "| 재무팀 | 박지훈 차장 | 대금 지급 협의 | 내선 2345 |" in doc


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
