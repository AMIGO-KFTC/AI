import pytest

from amigo_agent import AgentStateError, HandoverAgent

from conftest import PROFILE, FakeKB


def kinds(result):
    return [m["kind"] for m in result.messages]


def test_full_flow_stage_1_to_4(kb):
    agent = HandoverAgent()
    seen = []
    result = agent.start("s1", PROFILE, kb, on_event=seen.append)

    stages = [e["stage"] for e in seen if e["type"] == "stage"]
    assert stages[:2] == ["analyzing", "summary"]
    assert {e["key"] for e in seen if e["type"] == "slot"} == {"overview", "regular", "irregular", "stakeholders", "systems", "dept_notes"}
    assert kinds(result) == ["summary", "question"]
    snap = result.snapshot
    assert snap["waiting"] and snap["stage"] == "qna" and snap["question_count"] == 1
    assert snap["slots"]["overview"]["coverage"] == "sufficient"
    assert any(c["label"] == "업무정의서.pdf p.1" for it in snap["slots"]["overview"]["items"] for c in it["citations"])

    # 한 번에 하나의 질문: 질문 메시지에는 슬롯 하나와 gap_id 하나만 있다. 첫 질문은 협업관계의 '프로젝트명'
    question = result.messages[-1]
    assert question["meta"]["gap_id"] == snap["current_gap_id"] and question["meta"]["slot"] == "stakeholders"

    # 답변 → 교차 확인 메시지 + 확인 대기
    result = agent.send_message("s1", "CD공동망 고도화 프로젝트에서 협의했습니다", kb)
    assert kinds(result) == ["confirm"]
    assert "CD공동망 고도화" in result.messages[0]["content"]
    assert result.snapshot["pending"]["refs"]
    assert kb.added and "CD공동망 고도화" in kb.added[-1]  # 답변이 지식베이스에도 반영된다

    # 확인 → 다음 질문(이전 답변을 다시 정리하는 문구 없이 질문만): 시스템 접근 권한의 신청·이관 방법
    result = agent.send_message("s1", "네", kb)
    assert kinds(result) == ["question"]
    assert not result.messages[0]["content"].startswith(("**[", "["))  # 장 이름은 본문이 아니라 meta.slot 으로 알린다
    assert "확인했습니다" not in result.messages[0]["content"]
    assert result.snapshot["question_count"] == 2
    assert result.messages[0]["meta"]["slot"] == "systems"
    agent.send_message("s1", "신청은 내선 2345 로 연락합니다", kb)
    result = agent.send_message("s1", "네", kb)
    assert result.snapshot["question_count"] == 3

    # 건너뛰기
    result = agent.skip("s1", kb)
    skipped = [g for g in result.snapshot["gaps"] if g["status"] == "skipped"]
    assert len(skipped) == 1

    # 지금 문서 생성
    result = agent.finish("s1", kb)
    snap = result.snapshot
    assert snap["stage"] == "review" and snap["document_version"] >= 1 and snap["waiting"]
    version = snap["document_version"]
    doc = snap["document"]
    assert doc.startswith("# 업무 인수인계서")
    for heading in ("## 1. 업무 단위 요약 + 일정 명세표", "## 2. 정기업무", "## 3. 비정기 업무(프로젝트성)", "## 4. 시스템 접근 권한", "## 5. 부서별 추가 자료", "## 6. 확인 필요 사항", "## 7. 근거 자료"):
        assert heading in doc
    assert "## 1. 업무 개요" not in doc and "비정기 업무 협업관계 및 협의사항\n" not in doc  # 개요 장도, 협업관계 별도 장도 없다
    assert "CD공동망 고도화" in doc  # 맞는 프로젝트가 없는 협업관계는 '프로젝트 미지정'으로 모아 보인다
    assert "내선 2345" in doc
    assert "인계자 답변" in doc
    assert "(인계자 미확인)" in doc  # 건너뛴 질문은 확인 필요 사항으로 남는다

    # 검토 의견 → 문서 재작성(v2)
    result = agent.send_message("s1", "Google Analytics 권한은 기존 담당자가 직접 이관해 줍니다", kb)
    assert result.snapshot["document_version"] == version + 1


def test_correction_overwrites_last_recorded_fields(kb):
    agent = HandoverAgent()
    agent.start("s2", PROFILE, kb)
    # 첫 질문은 협업관계의 '프로젝트명'
    agent.send_message("s2", "홈페이지 리뉴얼 건입니다", kb)
    result = agent.send_message("s2", "아니, 개인정보처리방침 개정이에요", kb)
    assert kinds(result) == ["confirm"]
    assert "개인정보처리방침 개정" in result.messages[0]["content"]
    agent.send_message("s2", "네", kb)
    partners = agent.snapshot("s2")["slots"]["stakeholders"]["items"]
    finance = next(it for it in partners if it["title"].startswith("재무팀"))
    assert "개인정보처리방침 개정" in finance["fields"]["project"] and "홈페이지 리뉴얼" not in finance["fields"]["project"]
    assert finance["confirmed"] is True


def test_repeated_corrections_stop_after_two_rounds(kb):
    """정리 확인 → 고친 내용 확인 다음의 고침은 더 묻지 않고 반영한 뒤 다음 질문으로 넘어간다(무한 반복 방지)."""
    agent = HandoverAgent()
    agent.start("s5", PROFILE, kb)
    assert kinds(agent.send_message("s5", "홈페이지 리뉴얼 건입니다", kb)) == ["confirm"]
    first = agent.send_message("s5", "아니요, 개인정보처리방침 개정이에요", kb)
    assert kinds(first) == ["confirm"]
    assert "요, " not in first.messages[0]["content"]  # '아니요' 를 통째로 떼어 낸다
    second = agent.send_message("s5", "아니요, 웹 접근성 개선이에요", kb)
    assert kinds(second) == ["question"]
    assert second.messages[0]["content"].startswith("고쳐 주신 내용으로 반영해 둘게요.")
    assert not second.snapshot["pending"] and second.snapshot["question_count"] == 2
    finance = next(it for it in second.snapshot["slots"]["stakeholders"]["items"] if it["title"].startswith("재무팀"))
    assert "웹 접근성 개선" in finance["fields"]["project"]


def test_files_trigger_reanalysis_and_keep_user_answers(kb):
    agent = HandoverAgent()
    agent.start("s3", PROFILE, kb)
    agent.send_message("s3", "홈페이지 리뉴얼 건입니다", kb)
    agent.send_message("s3", "네", kb)
    kb.docs.append(
        {
            "chunk_id": "new1",
            "text": "| 부서 | 담당자 | 프로젝트 | 협업 내용 |\n|---|---|---|---|\n| 정보보호팀 | 최영희 주임 | 홈페이지 리뉴얼 | 웹방화벽 정책 |",
            "metadata": {"source_id": "new", "file_name": "연락망.xlsx", "source_type": "xlsx"},
        }
    )
    result = agent.notify_files("s3", kb, ["연락망.xlsx"])
    partners = {it["title"]: it for it in result.snapshot["slots"]["stakeholders"]["items"]}
    assert partners["정보보호팀 최영희 주임"]["fields"]["discussed"] == "웹방화벽 정책"  # 담당자 열은 협의 상대 이름에 붙는다
    finance = next(it for it in partners.values() if it["title"].startswith("재무팀"))
    assert finance["fields"]["project"] == "홈페이지 리뉴얼 건입니다"  # 재분석해도 인계자 답변은 유지
    assert result.messages[-1]["kind"] == "question"
    assert "새 자료(연락망.xlsx)를 반영했어요" in result.messages[-1]["content"]


def test_errors_before_start_and_question_limit(kb, monkeypatch):
    agent = HandoverAgent()
    with pytest.raises(AgentStateError):
        agent.send_message("nope", "안녕하세요", kb)
    monkeypatch.setenv("AMIGO_MAX_QUESTIONS", "1")
    limited = HandoverAgent()
    limited.start("s4", PROFILE, kb)
    result = limited.skip("s4", kb)
    assert result.snapshot["stage"] == "review"
    assert any("주요 질문을 모두 드렸어요" in m["content"] for m in result.messages)


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
    agent.start("s4", PROFILE, kb)
    agent.send_message("s4", "홈페이지 리뉴얼 건입니다", kb)
    before = agent.send_message("s4", "네", kb)
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
    assert remaining == [q for q in order if q in remaining]  # 남은 질문의 순서가 그대로
    assert all(g.get("rank") is not None for g in open_gaps(after.snapshot["gaps"]))
