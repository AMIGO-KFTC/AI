"""인수인계서는 정해진 템플릿(B. 세부 업무 / 비정기 업무 카드)만 싣는다: 어느 카드에도 맞지 않는 협업·비상 대응 항목을 양식에 없는 표로 싣지 않는다."""

from conftest import PROFILE, FakeKB

from amigo_agent import HandoverAgent
from amigo_agent.render import render_document, unlinked_children
from amigo_agent.state import empty_slots, upsert_item
from amigo_agent.template import slots_for
from htmltables import html_tables, texts

LEAVE = {"name": "김민수", "mode": "leave"}
B_LABELS = ["업무 유형", "주요 일정 및 처리사항", "현재 진행 상태", "업무 처리방법", "주의사항 및 후속조치", "참고자료"]


def _leave_slots():
    slots = empty_slots([s.key for s in slots_for(LEAVE)])
    for name in ("웹서비스 운영", "월간 보고"):
        cautions = "재무팀 박지훈 차장에게 문의" if name == "월간 보고" else ""
        upsert_item(slots["leave_tasks"], item_id="", title=name, fields={"name": name, "task_type": "정기", "cautions": cautions}, citations=[], origin="document")
    return slots


def _render(slots, gaps=()):
    return render_document(profile=LEAVE, slots=slots, gaps=list(gaps), sources=[], version=1)


def test_leave_document_has_the_template_a_items_in_order():
    md = _render(_leave_slots())
    assert "연결되는" not in md and "| 담당자 | 세부 업무명 | 대응·협조 내용 | 추후 고려사항 |" not in md  # 양식에 없는 별도 표가 없다
    card = md[md.index("### 1)"):md.index("### 2)")]
    labels = [line.split("|")[1].strip() for line in card.splitlines() if line.startswith("| ")]
    assert [l for l in labels if l in B_LABELS] == B_LABELS  # 장기휴가 A. 세부 업무의 6개 항목이 정해진 순서로
    assert "재무팀 박지훈 차장에게 문의" in md  # 비상 대응은 '주의사항 및 후속조치' 칸에 들어간다


DOCS = [
    {"file_name": "회의록.docx", "source_type": "docx", "text": "| 과제 | 진행 현황 |\n|---|---|\n| 홈페이지 리뉴얼 | RFP 작성 중 |"},
    {"file_name": "연락망.xlsx", "source_type": "xlsx", "text": "| 부서 | 담당자 | 프로젝트 | 협업 내용 |\n|---|---|---|---|\n| 재무팀 | 박지훈 차장 | 예산 시스템 개편 | 예산 협의 |"},
]


def test_interview_asks_which_card_an_unlinked_item_belongs_to_and_the_answer_links_it():
    agent = HandoverAgent()
    snap = agent.start("tc", PROFILE, FakeKB(DOCS)).snapshot
    link_gaps = [g for g in snap["gaps"] if g.get("link_field")]
    assert len(link_gaps) == 1 and link_gaps[0]["slot"] == "stakeholders" and "어느 프로젝트명에 해당하나요" in link_gaps[0]["question"] and "홈페이지 리뉴얼" in link_gaps[0]["question"]

    current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    while not current.get("link_field"):
        snap = agent.skip("tc", FakeKB(DOCS)).snapshot
        current = next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])
    agent.send_message("tc", "홈페이지 리뉴얼", FakeKB(DOCS))
    agent.send_message("tc", "네", FakeKB(DOCS))
    doc = agent.finish("tc", FakeKB(DOCS)).snapshot["document"]
    assert "어느 프로젝트명에 해당하는지" not in doc  # 연결되어 확인 필요 사항에서 빠진다
    card = next(t for t in html_tables(doc) if any(texts(r)[:1] == ["주요 협업관계 및 협의사항"] for r in t))
    assert any(texts(r) == ["재무팀 박지훈 차장", "예산 협의", "-"] for r in card)  # 홈페이지 리뉴얼 카드 안으로 들어갔다


def test_children_are_analyzed_after_parents_and_get_the_parent_names(monkeypatch):
    """붙는 장(프로젝트·세부 업무)을 먼저 분석하고, 그 이름 목록을 협업관계·비상 대응 분석 프롬프트에 준다."""
    from amigo_agent.engine.offline import OfflineEngine
    from amigo_agent.links import link_names_block, parent_names
    from amigo_agent.prompts import analysis_user
    from amigo_agent.template import SLOT_BY_KEY

    seen = {}
    real = OfflineEngine.analyze_slot

    def spy(self, spec, profile, kb):
        if spec.embed_into:
            seen[spec.key] = parent_names(profile.get("_link_slots") or {}, spec)
        return real(self, spec, profile, kb)

    monkeypatch.setattr(OfflineEngine, "analyze_slot", spy)
    HandoverAgent().start("ch", PROFILE, FakeKB())
    assert seen.get("stakeholders") == ["홈페이지 리뉴얼"]  # 자료에서 찾은 프로젝트 이름이 이미 있다

    slots = _leave_slots()
    assert link_names_block(slots, SLOT_BY_KEY["leave_tasks"]) == ""  # 세부 업무는 다른 장에 붙지 않는다
