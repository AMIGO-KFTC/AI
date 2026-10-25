import pytest

from amigo_agent import AgentStateError, HandoverAgent

from conftest import PROFILE, FakeKB


def kinds(result):
    return [m["kind"] for m in result.messages]


UNITS = {
    **PROFILE,
    "units": [
        {"name": "홈페이지 콘텐츠 관리", "regular": ["유지보수 월간 실적 검수", "보도자료 게시"], "irregular": ["홈페이지 리뉴얼"]},
        {"name": "유지보수 계약 관리", "regular": [], "irregular": []},
    ],
}


def current(snap):
    return next(g for g in snap["gaps"] if g["id"] == snap["current_gap_id"])


def test_full_flow_stage_1_to_4(kb):
    agent = HandoverAgent()
    seen = []
    result = agent.start("s1", UNITS, kb, on_event=seen.append)

    stages = [e["stage"] for e in seen if e["type"] == "stage"]
    assert stages[:2] == ["analyzing", "summary"]
    assert {e["key"] for e in seen if e["type"] == "slot"} == {"overview", "regular", "partners", "irregular", "milestones", "stakeholders", "systems", "dept_notes"}
    assert kinds(result) == ["summary", "question"]
    snap = result.snapshot
    assert snap["waiting"] and snap["stage"] == "qna" and snap["question_count"] == 1
    # 항목은 인계자가 고른 단위업무·입력한 세부업무만(자료의 다른 이름은 입력한 이름으로 맞춘다)
    assert [it["title"] for it in snap["slots"]["overview"]["items"]] == ["홈페이지 콘텐츠 관리", "유지보수 계약 관리"]
    assert [it["title"] for it in snap["slots"]["regular"]["items"]] == ["유지보수 월간 실적 검수", "보도자료 게시"]
    assert [it["title"] for it in snap["slots"]["irregular"]["items"]] == ["홈페이지 리뉴얼"]
    assert not [g for g in snap["gaps"] if g["slot"] == "overview"]  # 업무 단위 요약은 묻지 않는다

    # 첫 질문: 첫 세부업무의 중점 관리사항(자료에 없으면 예/아니요로 먼저 묻는다)
    question = result.messages[-1]
    assert question["meta"]["gap_id"] == snap["current_gap_id"] and question["meta"]["slot"] == "regular"
    assert question["meta"]["quick_replies"] == ["예", "아니요"] and question["meta"]["choice"] == "yesno"
    assert current(snap)["field"] == "key_points"

    # 아니요 → '해당 없음'으로 기록하고 다음 질문(의사결정, 예/아니요)
    result = agent.send_message("s1", "아니요", kb)
    assert kinds(result) == ["question"] and result.messages[0]["content"].startswith("'중점 관리사항'은(는) '해당 없음'으로 기록했어요.")
    item = result.snapshot["slots"]["regular"]["items"][0]
    assert item["fields"]["key_points"] == "해당 없음"
    assert current(result.snapshot)["field"] == "decisions"

    # 예 → 같은 질문 자리에서 노션 질문 예시로 이어서 묻는다(주관식)
    result = agent.send_message("s1", "예", kb)
    assert kinds(result) == ["question"] and "어떤 이유와 기준으로 결정했나요?" in result.messages[0]["content"]
    assert result.messages[0]["meta"]["quick_replies"] == ["모르겠어요 (건너뛰기)", "지금 문서 생성"]
    assert result.messages[0]["meta"]["gap_id"] == question["meta"]["gap_id"] or current(result.snapshot)["field"] == "decisions"

    # 답변 → 교차 확인 메시지 + 확인 대기
    result = agent.send_message("s1", "SLA 1시간 기준을 계약에 넣기로 결정했어요", kb)
    assert kinds(result) == ["confirm"] and result.snapshot["pending"]["refs"]
    assert kb.added and "SLA 1시간" in kb.added[-1]  # 답변이 지식베이스에도 반영된다

    # 확인 → 다음 질문(협업 유의사항 또는 향후 과제)
    result = agent.send_message("s1", "네", kb)
    assert kinds(result) == ["question"]
    assert not result.messages[0]["content"].startswith(("**[", "["))  # 장 이름은 본문이 아니라 meta.slot 으로 알린다
    assert "확인했습니다" not in result.messages[0]["content"]

    # 건너뛰기
    result = agent.skip("s1", kb)
    assert len([g for g in result.snapshot["gaps"] if g["status"] == "skipped"]) == 1

    # 지금 문서 생성
    result = agent.finish("s1", kb)
    snap = result.snapshot
    assert snap["stage"] == "review" and snap["document_version"] >= 1 and snap["waiting"]
    version = snap["document_version"]
    doc = snap["document"]
    assert doc.startswith("# 업무 인수인계서")
    for heading in ("## 1. 업무 단위 요약 + 일정 명세표", "## 2. 정기업무", "## 3. 비정기 업무(프로젝트성)", "## 4. 기타 자료"):
        assert heading in doc
    assert "## 5." not in doc and "확인 필요 사항" not in doc and "근거 자료" not in doc  # 양식에 없는 장은 싣지 않는다
    assert "SLA 1시간" in doc and "### 2) 보도자료 게시" in doc

    # 검토 의견 → 문서 재작성(v2)
    result = agent.send_message("s1", "Google Analytics 권한은 기존 담당자가 직접 이관해 줍니다", kb)
    assert result.snapshot["document_version"] == version + 1


def _to_follow_question(agent, thread, kb):
    agent.start(thread, UNITS, kb)
    agent.send_message(thread, "아니요", kb)  # 중점 관리사항 없음
    agent.send_message(thread, "예", kb)  # 의사결정 있음 → 주관식


def test_correction_overwrites_last_recorded_fields(kb):
    agent = HandoverAgent()
    _to_follow_question(agent, "s2", kb)
    agent.send_message("s2", "SLA 2시간 기준으로 결정", kb)
    result = agent.send_message("s2", "아니, SLA 1시간 기준이에요", kb)
    assert kinds(result) == ["confirm"] and "SLA 1시간" in result.messages[0]["content"]
    agent.send_message("s2", "네", kb)
    item = agent.snapshot("s2")["slots"]["regular"]["items"][0]
    assert "SLA 1시간" in item["fields"]["decisions"] and "2시간" not in item["fields"]["decisions"]
    assert item["confirmed"] is True


def test_repeated_corrections_stop_after_two_rounds(kb):
    """정리 확인 → 고친 내용 확인 다음의 고침은 더 묻지 않고 반영한 뒤 다음 질문으로 넘어간다(무한 반복 방지)."""
    agent = HandoverAgent()
    _to_follow_question(agent, "s5", kb)
    assert kinds(agent.send_message("s5", "SLA 2시간 기준으로 결정", kb)) == ["confirm"]
    first = agent.send_message("s5", "아니요, SLA 1시간 기준이에요", kb)
    assert kinds(first) == ["confirm"]
    assert "요, " not in first.messages[0]["content"]  # '아니요' 를 통째로 떼어 낸다
    second = agent.send_message("s5", "아니요, SLA 30분 기준이에요", kb)
    assert kinds(second) == ["question"]
    assert second.messages[0]["content"].startswith("고쳐 주신 내용으로 반영해 둘게요.")
    assert not second.snapshot["pending"]
    assert "SLA 30분" in second.snapshot["slots"]["regular"]["items"][0]["fields"]["decisions"]


def test_files_trigger_reanalysis_and_keep_user_answers(kb):
    agent = HandoverAgent()
    _to_follow_question(agent, "s3", kb)
    agent.send_message("s3", "SLA 1시간 기준으로 결정", kb)
    agent.send_message("s3", "네", kb)
    kb.docs.append(
        {
            "chunk_id": "new1",
            "text": "| 부서 | 담당자 | 프로젝트 | 협업 내용 |\n|---|---|---|---|\n| 정보보호팀 | 최영희 주임 | 홈페이지 리뉴얼 | 웹방화벽 정책 |",
            "metadata": {"source_id": "new", "file_name": "연락망.xlsx", "source_type": "xlsx"},
        }
    )
    result = agent.notify_files("s3", kb, ["연락망.xlsx"])
    rows = {it["fields"].get("name"): it for it in result.snapshot["slots"]["stakeholders"]["items"]}
    assert rows["최영희 주임"]["fields"]["role"] == "웹방화벽 정책" and rows["최영희 주임"]["fields"]["org"] == "정보보호팀"
    assert [it["title"] for it in result.snapshot["slots"]["regular"]["items"]] == ["유지보수 월간 실적 검수", "보도자료 게시"]  # 입력한 세부업무만
    assert "SLA 1시간" in result.snapshot["slots"]["regular"]["items"][0]["fields"]["decisions"]  # 재분석해도 인계자 답변은 유지
    assert result.messages[-1]["kind"] == "question"
    assert "새 자료(연락망.xlsx)를 반영했어요" in result.messages[-1]["content"]


def test_errors_before_start_and_no_question_limit(kb):
    agent = HandoverAgent()
    with pytest.raises(AgentStateError):
        agent.send_message("nope", "안녕하세요", kb)
    # 질문 수는 제한하지 않는다: 남은 질문이 있으면 끝까지 묻는다(암묵지를 확인할 질문을 줄이지 않는다)
    result = agent.start("s4", PROFILE, kb)
    total = sum(1 for g in result.snapshot["gaps"] if g["status"] in ("open", "asked"))
    for _ in range(total - 1):
        result = agent.skip("s4", kb)
        assert result.snapshot["stage"] == "qna"
    result = agent.skip("s4", kb)
    assert result.snapshot["stage"] == "review"
    assert not any("주요 질문을 모두 드렸어요" in m["content"] for m in result.messages)


def test_no_gaps_goes_straight_to_document():
    kb = FakeKB(docs=[])
    agent = HandoverAgent()
    result = agent.start("s5", PROFILE, kb)
    # 자료가 없으면 모든 장이 '부족' → 질문부터 시작
    assert result.snapshot["stage"] == "qna"
    assert all(s["coverage"] == "missing" for s in result.snapshot["slots"].values())


def test_sqlite_checkpoint_survives_restart(tmp_path, kb):
    db = tmp_path / "agent.sqlite"
    first = HandoverAgent.with_sqlite(db)
    first.start("persist", PROFILE, kb)
    question_id = first.snapshot("persist")["current_gap_id"]

    second = HandoverAgent.with_sqlite(db)  # 서버 재시작 가정
    snap = second.snapshot("persist")
    assert snap["waiting"] and snap["current_gap_id"] == question_id
    result = second.send_message("persist", "재무팀 박지훈 차장 내선 2345", second_kb := kb)
    assert result.messages[0]["kind"] == "confirm"
    assert second_kb.added


def test_mermaid_diagram_lists_stages():
    diagram = HandoverAgent().mermaid()
    for node in ("prepare", "analyze_slot", "summarize", "ask", "wait_user", "interpret", "compose"):
        assert node in diagram


def test_passwords_are_never_stored(kb):
    """보안 서약: 비밀번호 값은 대화·지식베이스·문서 어디에도 남기지 않고, 수기 작성을 안내한다."""
    agent = HandoverAgent()
    agent.start("pw", PROFILE, kb)
    result = agent.send_message("pw", "재무팀 박지훈 차장 내선 2345, CMS 비밀번호는 Abcd1234! 입니다", kb)
    assert any("직접 적어 주세요" in m["content"] for m in result.messages)
    assert all("Abcd1234" not in m["content"] for m in result.messages)
    assert kb.added and all("Abcd1234" not in t for t in kb.added)
    assert "Abcd1234" not in str(result.snapshot)


def test_scrub_credentials_keeps_descriptions():
    from amigo_agent.textkit import scrub_credentials

    assert scrub_credentials("GA 비밀번호는 보안USB에 보관")[1] is False
    clean, found = scrub_credentials("ID는 admin, PW: p@ssw0rd 입니다")
    assert found and "p@ssw0rd" not in clean


def test_file_upload_keeps_question_order(kb):
    """대화 중 자료를 올려도 질문 순서가 처음으로 돌아가지 않고, 방금 물은 질문을 번호 증가 없이 다시 묻는다."""
    from amigo_agent.state import open_gaps

    agent = HandoverAgent()
    agent.start("s4", UNITS, kb)
    before = agent.send_message("s4", "아니요", kb)
    asked = before.messages[-1]["content"]
    order = [g["question"] for g in open_gaps(before.snapshot["gaps"])]
    count = before.snapshot["question_count"]
    kb.docs.append(
        {
            "chunk_id": "new2",
            "text": "| 부서 | 담당자 | 프로젝트 | 협업 내용 |\n|---|---|---|---|\n| 정보보호팀 | 최영희 주임 | 홈페이지 리뉴얼 | 웹방화벽 정책 |",
            "metadata": {"source_id": "new", "file_name": "연락망.xlsx", "source_type": "xlsx"},
        }
    )
    after = agent.notify_files("s4", kb, ["연락망.xlsx"])
    assert asked.split("\n\n")[-1] in after.messages[-1]["content"]  # 방금 물은 질문을 다시 묻는다
    assert after.snapshot["question_count"] == count  # 같은 질문이라 번호는 그대로
    remaining = [g["question"] for g in open_gaps(after.snapshot["gaps"])]
    assert [q for q in remaining if q in order] == [q for q in order if q in remaining]  # 원래 있던 질문끼리의 순서는 그대로
    assert all(g.get("rank") is not None for g in open_gaps(after.snapshot["gaps"]))


def test_questions_are_asked_in_template_order_one_task_at_a_time(kb):
    """양식 순서대로 묻는다: 장 순서(정기 → 비정기) 안에서 세부업무 하나를 다 묻고 다음 세부업무로, 세부업무 안에서는 표의 행 순서대로."""
    from amigo_agent.template import SLOT_BY_KEY

    agent = HandoverAgent()
    snap = agent.start("order", UNITS, kb).snapshot
    asked = []
    while snap["stage"] == "qna" and snap["waiting"]:
        gap = current(snap)
        if gap["slot"] == "dept_notes":  # 기타 자료(부서별 추가 자료)는 마지막 장
            asked.append((2, 0, 0))
            snap = agent.skip("order", kb).snapshot
            continue
        spec = SLOT_BY_KEY[gap["slot"]]
        parent_key = spec.embed_into or spec.key
        parent = SLOT_BY_KEY[parent_key]
        item_id = gap.get("parent_id") or gap.get("item_id")
        index = [it["id"] for it in snap["slots"][parent_key]["items"]].index(item_id)
        keys = [f.key for f in parent.fields]
        row = keys.index(spec.embed_after) + 0.5 if spec.embed_into else keys.index(gap["field"]) if gap.get("field") in keys else -1
        asked.append(((0 if parent_key == "regular" else 1), index, row))
        snap = agent.skip("order", kb).snapshot
    assert asked and asked == sorted(asked)
    assert {a[0] for a in asked} == {0, 1, 2} and len({(a[0], a[1]) for a in asked if a[0] < 2}) == 3  # 정기 2건, 비정기 1건, 그다음 기타 자료
