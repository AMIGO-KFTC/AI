"""팀 필수 항목 보장: 자료에서 확인되지 않은 필수 팀 항목은 질문이 반드시 나오고 맨 앞에 온다."""

from amigo_agent import HandoverAgent
from amigo_agent.team_items import ensure_team_item_gaps, is_covered, required_team_items, team_item_question

from conftest import PROFILE

REQUIRED = {"name": "라이선스", "instruction": "제품명, 만료일, 갱신 방법, 결제 부서", "required": True}
OPTIONAL = {"name": "보안 점검", "instruction": "", "required": False}
KEYS = ["overview", "regular", "irregular", "stakeholders", "systems", "dept_notes"]


def profile(*items):
    return {"name": "김민수", "team_guidelines": {"items": list(items), "notes": ""}}


def slot_with(title="", **fields):
    return {"items": [{"id": "it-1", "title": title, "fields": fields}] if (title or fields) else []}


def gap(slot="regular", question="질문", status="open", rank=0, **extra):
    return {"id": f"gap-{slot}-{rank}-{status}", "slot": slot, "question": question, "description": "", "priority": 2, "status": status, "rank": rank, **extra}


def test_nothing_changes_without_required_items():
    gaps = [gap()]
    assert ensure_team_item_gaps({"name": "x"}, {}, gaps, slot_keys=KEYS, limit=12) is gaps
    assert ensure_team_item_gaps(profile(OPTIONAL), {}, gaps, slot_keys=KEYS, limit=12) is gaps  # 필수가 아닌 항목은 만들지 않는다
    assert ensure_team_item_gaps(profile(REQUIRED), {}, gaps, slot_keys=["overview"], limit=12) is gaps  # 담을 장(부서별 추가 자료)이 없는 구성이면 건드리지 않는다
    assert required_team_items(profile(REQUIRED, OPTIONAL)) == [REQUIRED]


def test_missing_required_item_gets_a_question_and_goes_first():
    gaps = [gap("regular", "정기 업무 질문", rank=0), gap("systems", "시스템 질문", rank=1)]
    result = ensure_team_item_gaps(profile(REQUIRED), {k: {"items": []} for k in KEYS}, gaps, slot_keys=KEYS, limit=12)
    team = result[0]
    assert team["slot"] == "dept_notes" and team["team_item"] == "라이선스" and team["status"] == "open" and team["priority"] == 1
    assert "'라이선스'" in team["question"] and "제품명, 만료일, 갱신 방법, 결제 부서" in team["question"] and "없음" in team["question"]
    assert team["description"].startswith("팀 필수 항목 '라이선스'")
    assert [g["question"] for g in result[1:]] == ["정기 업무 질문", "시스템 질문"]
    assert [g["rank"] for g in result] == [0, 1, 2]  # 순서가 rank 로도 남는다


def test_covered_items_are_not_asked_again():
    slots = {"dept_notes": slot_with("외주 업체 관리", topic="외주 관리", detail="웹솔루션과 계약")}
    assert is_covered("외주 관리", slots, KEYS)  # 주제 필드
    assert is_covered("외주업체", slots, KEYS)  # 띄어쓰기·기호는 무시하고 제목에서도 찾는다
    assert is_covered("라이선스", {"regular": slot_with(name="라이선스 갱신")}, KEYS)  # 다른 장의 이름 필드도 인정한다
    assert not is_covered("라이선스", slots, KEYS)
    assert ensure_team_item_gaps(profile({"name": "외주 관리", "required": True}), slots, [], slot_keys=KEYS, limit=12) == []
    # 본문(detail)에 단어만 나오는 것은 다뤄진 것으로 치지 않는다
    assert not is_covered("계약", {"dept_notes": slot_with("외주", topic="외주", detail="계약 기간")}, KEYS)


def test_same_slot_question_about_the_item_is_replaced_not_duplicated():
    other_slot = gap("systems", "라이선스 서버 접근은 어떻게 신청하나요?", rank=2)  # 다른 장의 질문은 그대로 둔다
    gaps = [gap("regular", "정기 업무 질문", rank=0), gap("dept_notes", "챙겨야 할 사항(외주 관리, 라이선스 등)이 있나요?", rank=1), other_slot]
    result = ensure_team_item_gaps(profile(REQUIRED), {k: {"items": []} for k in KEYS}, gaps, slot_keys=KEYS, limit=12)
    assert len(result) == 3
    team = result[0]
    assert team["team_item"] == "라이선스" and team["slot"] == "dept_notes" and "제품명, 만료일, 갱신 방법, 결제 부서" in team["question"]  # 작성 안내가 든 질문 하나로 대신한다
    assert [g["question"] for g in result[1:]] == ["정기 업무 질문", "라이선스 서버 접근은 어떻게 신청하나요?"]
    assert not any("챙겨야 할 사항" in g["question"] for g in result)


def test_asked_before_is_never_recreated_and_calls_are_idempotent():
    slots = {k: {"items": []} for k in KEYS}
    for status in ("asked", "answered", "resolved", "skipped"):  # 한 번이라도 물었으면(답변·건너뜀 포함) 다시 만들지 않는다
        gaps = [gap("dept_notes", team_item_question("라이선스", ""), status=status, team_item="라이선스")]
        assert len(ensure_team_item_gaps(profile(REQUIRED), slots, gaps, slot_keys=KEYS, limit=12)) == 1
    once = ensure_team_item_gaps(profile(REQUIRED), slots, [gap()], slot_keys=KEYS, limit=12)
    twice = ensure_team_item_gaps(profile(REQUIRED), slots, once, slot_keys=KEYS, limit=12)
    assert len(twice) == 2 and sum(1 for g in twice if g.get("team_item")) == 1


def test_limit_caps_the_number_of_new_team_questions_and_order_is_kept():
    items = [{"name": f"항목{i}", "required": True} for i in range(5)]
    result = ensure_team_item_gaps(profile(*items), {k: {"items": []} for k in KEYS}, [gap()], slot_keys=KEYS, limit=3)
    team = [g for g in result if g.get("team_item")]
    assert [g["team_item"] for g in team] == ["항목0", "항목1", "항목2"] and result[:3] == team  # 지정한 순서대로, 상한까지
    assert result[-1]["question"] == "질문"


def test_graph_asks_the_missing_required_item_first_and_does_not_reask(kb):
    agent = HandoverAgent()
    guided = {**PROFILE, "team_guidelines": {"items": [REQUIRED], "notes": ""}}
    result = agent.start("t1", guided, kb)
    question = result.messages[-1]
    assert question["meta"]["slot"] == "dept_notes" and "라이선스" in question["content"]  # 자료에 없는 필수 항목이 첫 질문
    team = [g for g in result.snapshot["gaps"] if g.get("team_item")]
    assert len(team) == 1 and team[0]["id"] == result.snapshot["current_gap_id"] and team[0]["rank"] == 0

    # 새 자료를 올려 재분석해도 필수 질문은 사라지지 않고 하나만 남는다
    result = agent.notify_files("t1", kb, ["새자료.pdf"])
    gaps = [g for g in result.snapshot["gaps"] if g.get("team_item")]
    assert len(gaps) == 1 and gaps[0]["status"] in ("open", "asked")

    # 건너뛰면 확인 필요 사항에 남고, 다시 만들어지지 않는다
    result = agent.skip("t1", kb)
    skipped = [g for g in result.snapshot["gaps"] if g.get("team_item")]
    assert len(skipped) == 1 and skipped[0]["status"] == "skipped"
    result = agent.notify_files("t1", kb, ["또새자료.pdf"])
    assert len([g for g in result.snapshot["gaps"] if g.get("team_item")]) == 1
    result = agent.finish("t1", kb)
    assert "라이선스" in result.snapshot["document"].split("## 6. 확인 필요 사항")[1]


def test_graph_without_guidelines_is_unchanged(kb):
    result = HandoverAgent().start("t2", PROFILE, kb)
    assert not any(g.get("team_item") for g in result.snapshot["gaps"])
    assert result.messages[-1]["meta"]["slot"] == "stakeholders"  # 지침이 없으면 첫 질문은 기존과 똑같다
