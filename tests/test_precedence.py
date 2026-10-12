"""정보 충돌 우선순위: 인계자 최신 답변 > 작성일이 더 최근인 공식 문서 > 회의록·메일 > 날짜 없는 참고자료."""

from datetime import date

from amigo_agent import HandoverAgent, render_document
from amigo_agent.kb import Evidence
from amigo_agent.precedence import beats, classify, parse_ts
from amigo_agent.state import empty_slots, upsert_item

from conftest import PROFILE, FakeKB


def cite(meta: dict, label: str = "자료") -> dict:
    ev = Evidence(chunk_id=label, text="텍스트", citation=label, source_id=label, metadata=meta)
    return ev.to_citation()


def put(slot, value, meta=None, origin="document", label="자료", citations=None):
    cites = citations if citations is not None else [cite(meta or {}, label)]
    return upsert_item(slot, item_id="", title="재무팀", fields={"party": "재무팀", "contact": value}, citations=cites, origin=origin)


def stakeholders():
    return empty_slots()["stakeholders"]


def contact(slot) -> str:
    return slot["items"][0]["fields"]["contact"]


OFFICIAL = {"file_name": "업무정의서.pdf", "source_type": "pdf"}
MINUTES = {"file_name": "주간회의록.docx", "source_type": "docx"}
MAIL = {"file_name": "요청.eml", "source_type": "eml"}
REFERENCE = {"file_name": "메모.txt", "source_type": "txt"}


def test_parse_ts_normalizes_formats_to_the_minute():
    assert parse_ts("2026.10.11 14:05") == "2026-10-11T14:05"
    assert parse_ts("2026-10-11") == "2026-10-11T00:00"
    assert parse_ts("2026-10-11T14:05:59") == "2026-10-11T14:05"
    assert parse_ts("Sun, 11 Oct 2026 00:24:42 +0900") is not None
    assert parse_ts("날짜 아님") is None and parse_ts("") is None and parse_ts(None) is None


def test_classify_documents():
    assert classify({**OFFICIAL, "created_at": "2026-09-01"})["rank"] == 2
    assert classify({**OFFICIAL, "modified_at": "2026-09-01 10:00"})["ts"] == "2026-09-01T10:00"  # 작성일 없으면 수정일
    assert classify({**OFFICIAL})["rank"] == 4  # 날짜 없는 문서는 참고자료
    assert classify({**MINUTES, "created_at": "2026-09-22"})["rank"] == 3
    assert classify({**MINUTES})["rank"] == 3  # 회의록은 날짜가 없어도 3순위
    assert classify({**MAIL, "sent_at": "2026-10-01 09:00"}) == {"rank": 3, "ts": "2026-10-01T09:00", "doc_class": "mail"}
    assert classify({**OFFICIAL, "created_at": "2026-09-01", "doc_class": "reference"})["rank"] == 4  # 직접 지정 우선
    assert classify({"source_type": "chat"})["rank"] == 1


def test_newer_official_document_wins_even_by_minutes():
    slot = stakeholders()
    put(slot, "내선 1111", {**OFFICIAL, "created_at": "2026-09-01 10:00"}, label="구 문서")
    put(slot, "내선 2222", {**OFFICIAL, "created_at": "2026-09-01 10:01"}, label="신 문서")
    assert contact(slot) == "내선 2222"
    put(slot, "내선 3333", {**OFFICIAL, "created_at": "2026-09-01 09:59"}, label="더 옛 문서")
    assert contact(slot) == "내선 2222"  # 더 옛 문서는 이기지 못한다
    record = slot["items"][0]["conflicts"][0]
    assert record["applied"] == "내선 2222" and record["dropped"] == "내선 1111" and record["applied_label"] == "신 문서"
    assert "더 최근" in record["reason"]


def test_rank_beats_recency():
    slot = stakeholders()
    put(slot, "내선 1111", {**OFFICIAL, "created_at": "2026-01-01"}, label="공식 문서")
    put(slot, "내선 2222", {**MINUTES, "created_at": "2026-10-01"}, label="회의록")  # 더 최신이지만 3순위
    assert contact(slot) == "내선 1111"
    put(slot, "내선 3333", {**MAIL, "sent_at": "2026-10-02 09:00"}, label="메일")  # 같은 3순위 아님, 공식이 이김
    assert contact(slot) == "내선 1111"


def test_minutes_and_mail_share_a_rank_and_compare_by_time():
    slot = stakeholders()
    put(slot, "내선 1111", {**MINUTES, "created_at": "2026-10-01 09:00"}, label="회의록")
    put(slot, "내선 2222", {**MAIL, "sent_at": "2026-10-01 09:30"}, label="메일")
    assert contact(slot) == "내선 2222"


def test_undated_reference_is_weakest_and_dated_beats_undated():
    slot = stakeholders()
    put(slot, "내선 1111", REFERENCE, label="메모")
    put(slot, "내선 2222", {**MINUTES, "created_at": "2020-01-01"}, label="옛 회의록")
    assert contact(slot) == "내선 2222"
    put(slot, "내선 3333", REFERENCE, label="다른 메모")
    assert contact(slot) == "내선 2222"


def test_same_rank_same_time_keeps_first_value_but_records_conflict():
    slot = stakeholders()
    put(slot, "내선 1111", REFERENCE, label="메모 A")
    put(slot, "내선 2222", REFERENCE, label="메모 B")
    assert contact(slot) == "내선 1111"
    assert "먼저 확인된 값" in slot["items"][0]["conflicts"][0]["reason"]


def test_user_answer_beats_newer_document_and_later_answers_override_earlier_ones():
    slot = stakeholders()
    user = {"source_id": "chat", "label": "인계자 답변", "rank": 1, "ts": "2026-10-11T10:00:00", "doc_class": "user"}
    put(slot, "내선 1111", {**OFFICIAL, "created_at": "2026-10-10"}, label="공식 문서")
    put(slot, "내선 2222", origin="user", citations=[user])
    assert contact(slot) == "내선 2222"
    put(slot, "내선 3333", {**OFFICIAL, "created_at": "2026-10-11 12:00"}, label="더 최신 공식 문서")
    assert contact(slot) == "내선 2222"  # 문서가 더 최신이어도 인계자 답변이 우선
    put(slot, "내선 4444", origin="user", citations=[{**user, "ts": "2026-10-11T10:00:00"}])  # 같은 시각의 번복
    assert contact(slot) == "내선 4444"
    put(slot, "내선 5555", origin="user", citations=[{**user, "ts": "2026-10-11T09:59:00"}])  # 더 이른 답변은 이기지 못한다
    assert contact(slot) == "내선 4444"


def test_empty_fields_are_filled_regardless_of_rank():
    slot = stakeholders()
    put(slot, "내선 1111", {**OFFICIAL, "created_at": "2026-10-10"})
    upsert_item(slot, item_id="", title="재무팀", fields={"person": "박지훈 차장"}, citations=[cite(REFERENCE)], origin="document")
    assert slot["items"][0]["fields"]["person"] == "박지훈 차장" and contact(slot) == "내선 1111"
    assert not slot["items"][0].get("conflicts")


def test_beats_handles_missing_meta():
    assert beats({"rank": 4, "ts": None}, None)
    assert not beats({"rank": 4, "ts": None}, {"rank": 4, "ts": None})
    assert beats({"rank": 1, "ts": None}, {"rank": 1, "ts": None})


TABLE = "| 부서 | 담당자 | 협업 내용 | 연락처 |\n|---|---|---|---|\n| 정보보호팀 | 최영희 주임 | 웹방화벽 정책 | {phone} |"


def _doc(name, phone, **meta):
    return {"file_name": name, "source_type": meta.pop("source_type", "xlsx"), "text": TABLE.format(phone=phone), **meta}


def test_offline_pipeline_applies_precedence_between_documents():
    kb = FakeKB(docs=[_doc("연락망_구.xlsx", "내선 1111", created_at="2026-09-01 10:00"), _doc("연락망_신.xlsx", "내선 2222", created_at="2026-09-01 10:30")])
    agent = HandoverAgent()
    agent.start("p1", PROFILE, kb)
    items = agent.snapshot("p1")["slots"]["stakeholders"]["items"]
    team = next(it for it in items if it["title"] == "정보보호팀")
    assert team["fields"]["contact"] == "내선 2222"
    assert team["conflicts"] and team["conflicts"][0]["dropped"] == "내선 1111"
    doc = agent.finish("p1", kb).snapshot["document"]
    assert "**자동으로 처리한 정보 충돌**" in doc
    assert "정보보호팀 · 연락처: '내선 2222'(연락망_신.xlsx) 적용, '내선 1111'(연락망_구.xlsx) 제외" in doc


def test_user_answer_survives_reanalysis_with_newer_document():
    kb = FakeKB(docs=[_doc("연락망.xlsx", "-", created_at="2026-09-01")])  # 처음 자료에는 연락처가 비어 있다
    agent = HandoverAgent()
    agent.start("p2", PROFILE, kb)
    agent.send_message("p2", "정보보호팀 최영희 주임 연락처는 내선 9999 입니다", kb)
    agent.send_message("p2", "네", kb)
    kb.docs.append({"chunk_id": "n1", "text": TABLE.format(phone="내선 3333"), "metadata": {"source_id": "n1", "file_name": "연락망_최신.xlsx", "source_type": "xlsx", "created_at": "2099-01-01"}})
    result = agent.notify_files("p2", kb, ["연락망_최신.xlsx"])
    team = next(it for it in result.snapshot["slots"]["stakeholders"]["items"] if it["title"] == "정보보호팀")
    assert team["fields"]["contact"] == "내선 9999"  # 훨씬 최신 공식 문서가 와도 인계자 답변이 우선


def test_render_lists_resolved_conflicts_only_when_present():
    slots = empty_slots()
    base = dict(profile={"name": "김민수"}, slots=slots, gaps=[], extras={"overview": "개요", "key_points": []}, sources=[], version=1, today=date(2026, 10, 11))
    assert "자동으로 처리한 정보 충돌" not in render_document(**base)
    slot = slots["stakeholders"]
    put(slot, "내선 1111", {**OFFICIAL, "created_at": "2026-09-01"}, label="공식 문서")
    put(slot, "내선 2222", {**MINUTES, "created_at": "2026-10-01"}, label="회의록")
    md = render_document(**base)
    assert "'내선 1111'(공식 문서) 적용, '내선 2222'(회의록) 제외 — 공식 문서 우선" in md


# ---------------------------------------------------------------- resolve_conflicts 노드(후보 모으기 → 확정)
def _deferred(slot, value, meta, label):
    return upsert_item(slot, item_id="", title="재무팀", fields={"party": "재무팀", "contact": value}, citations=[cite(meta, label)], origin="document", defer=True)


def test_deferred_candidates_are_resolved_later_with_same_result_as_immediate():
    from amigo_agent.state import resolve_slots

    inputs = [
        ("내선 1111", {**MINUTES, "created_at": "2026-10-01 09:00"}, "회의록"),
        ("내선 2222", {**OFFICIAL, "created_at": "2026-01-01"}, "공식 문서"),
        ("내선 3333", {**MAIL, "sent_at": "2026-10-05 09:00"}, "메일"),
        ("내선 4444", REFERENCE, "메모"),
    ]
    immediate, deferred = stakeholders(), stakeholders()
    for value, meta, label in inputs:
        put(immediate, value, meta, label=label)
        _deferred(deferred, value, meta, label)
    assert "candidates" in deferred["items"][0]  # 확정 전에는 후보만 모여 있다
    assert len(deferred["items"][0]["candidates"]["contact"]) == 4
    assert resolve_slots({"stakeholders": deferred}) == ["stakeholders"]
    item = deferred["items"][0]
    assert "candidates" not in item
    assert item["fields"]["contact"] == contact(immediate) == "내선 2222"
    assert [(c["applied"], c["dropped"]) for c in item["conflicts"]] == [(c["applied"], c["dropped"]) for c in immediate["items"][0]["conflicts"]]
    assert resolve_slots({"stakeholders": deferred}) == []  # 이미 확정한 뒤에는 할 일이 없다


def test_graph_has_resolve_conflicts_nodes_at_each_merge_point():
    diagram = HandoverAgent().mermaid()
    for edge in (
        "analyze_slot --> resolve_conflicts;",
        "resolve_conflicts --> collect_gaps;",
        "interpret --> resolve_answer;",
        "resolve_answer -.-> wait_user;",
        "reanalyze --> resolve_reanalysis;",
        "resolve_reanalysis -.-> choose_next;",
    ):
        assert edge in diagram
    assert "analyze_slot --> collect_gaps;" not in diagram and "interpret -.->" not in diagram  # 확정 노드를 건너뛰는 길은 없다


def test_resolve_node_reemits_slots_without_leftover_candidates():
    kb = FakeKB(docs=[_doc("연락망.xlsx", "내선 1111", created_at="2026-09-01")])
    agent = HandoverAgent()
    seen = []
    agent.start("g1", PROFILE, kb, on_event=seen.append)
    slot_events = [e for e in seen if e["type"] == "slot" and e["key"] == "stakeholders"]
    assert len(slot_events) >= 2  # 분석 직후 한 번, 충돌 확정 후 다시 한 번
    assert any("candidates" in it for it in slot_events[0]["slot"]["items"])  # 분석 직후에는 후보 상태
    assert all("candidates" not in it for it in slot_events[-1]["slot"]["items"])  # 확정 후에는 후보가 남지 않는다
    snap_items = agent.snapshot("g1")["slots"]["stakeholders"]["items"]
    assert all("candidates" not in it for it in snap_items)
