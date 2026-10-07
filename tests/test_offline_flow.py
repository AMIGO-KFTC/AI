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
    assert {e["key"] for e in seen if e["type"] == "slot"} == {"duties", "recurring", "projects", "contacts", "systems", "issues"}
    assert kinds(result) == ["summary", "question"]
    snap = result.snapshot
    assert snap["waiting"] and snap["stage"] == "qna" and snap["question_count"] == 1
    assert snap["slots"]["duties"]["coverage"] == "sufficient"
    assert any(c["label"] == "업무정의서.pdf p.1" for it in snap["slots"]["duties"]["items"] for c in it["citations"])

    # 한 번에 하나의 질문: 질문 메시지에는 슬롯 하나와 gap_id 하나만 있다
    question = result.messages[-1]
    assert question["meta"]["gap_id"] == snap["current_gap_id"]

    # 답변 → 교차 확인 메시지 + 확인 대기
    result = agent.send_message("s1", "재무팀 박지훈 차장 연락처는 내선 2345 입니다", kb)
    assert kinds(result) == ["confirm"]
    assert "내선 2345" in result.messages[0]["content"]
    assert result.snapshot["pending"]["refs"]
    assert kb.added and "내선 2345" in kb.added[-1]  # 답변이 지식베이스에도 반영된다

    # 확인 → 다음 질문(ack 포함)
    result = agent.send_message("s1", "네", kb)
    assert kinds(result) == ["question"]
    assert result.messages[0]["content"].startswith("확인했습니다.")
    assert result.snapshot["question_count"] == 2

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
    for heading in ("## 2. 담당 업무", "## 5. 협업 관계", "## 8. 확인 필요 사항", "## 9. 근거 자료"):
        assert heading in doc
    assert "내선 2345" in doc
    assert "인계자 답변" in doc
    assert "(인계자 미확인)" in doc  # 건너뛴 질문은 확인 필요 사항으로 남는다

    # 검토 의견 → 문서 재작성(v2)
    result = agent.send_message("s1", "Google Analytics 권한은 기존 담당자가 직접 이관해 줍니다", kb)
    assert result.snapshot["document_version"] == version + 1


def test_correction_overwrites_last_recorded_fields(kb):
    agent = HandoverAgent()
    agent.start("s2", PROFILE, kb)
    agent.send_message("s2", "재무팀 박지훈 차장은 내선 2345", kb)
    result = agent.send_message("s2", "아니, 내선 2346 이에요", kb)
    assert kinds(result) == ["confirm"]
    assert "내선 2346" in result.messages[0]["content"]
    agent.send_message("s2", "네", kb)
    contacts = agent.snapshot("s2")["slots"]["contacts"]["items"]
    finance = next(it for it in contacts if it["title"] == "재무팀")
    assert finance["fields"]["contact"] == "내선 2346"
    assert finance["confirmed"] is True



def test_repeated_corrections_stop_after_two_rounds(kb):
    """정리 확인 → 고친 내용 확인 다음의 고침은 더 묻지 않고 반영한 뒤 다음 질문으로 넘어간다(무한 반복 방지)."""
    agent = HandoverAgent()
    agent.start("s5", PROFILE, kb)
    assert kinds(agent.send_message("s5", "재무팀 박지훈 차장 연락처는 내선 2345 입니다", kb)) == ["confirm"]
    first = agent.send_message("s5", "아니요, 내선 2346 이에요", kb)
    assert kinds(first) == ["confirm"]
    assert "요, " not in first.messages[0]["content"]  # '아니요' 를 통째로 떼어 낸다
    second = agent.send_message("s5", "아니요, 내선 2347 이에요", kb)
    assert kinds(second) == ["question"]
    assert second.messages[0]["content"].startswith("고쳐 주신 내용으로 반영해 둘게요.")
    assert not second.snapshot["pending"] and second.snapshot["question_count"] == 2
    finance = next(it for it in second.snapshot["slots"]["contacts"]["items"] if it["title"] == "재무팀")
    assert finance["fields"]["contact"] == "내선 2347"

def test_files_trigger_reanalysis_and_keep_user_answers(kb):
    agent = HandoverAgent()
    agent.start("s3", PROFILE, kb)
    agent.send_message("s3", "재무팀 박지훈 차장 연락처는 내선 2345", kb)
    agent.send_message("s3", "네", kb)
    kb.docs.append(
        {
            "chunk_id": "new1",
            "text": "| 부서 | 담당자 | 협업 내용 | 연락처 |\n|---|---|---|---|\n| 정보보호팀 | 최영희 주임 | 웹방화벽 정책 | 내선 4567 |",
            "metadata": {"source_id": "new", "file_name": "연락망.xlsx", "source_type": "xlsx"},
        }
    )
    result = agent.notify_files("s3", kb, ["연락망.xlsx"])
    contacts = result.snapshot["slots"]["contacts"]["items"]
    titles = {it["title"]: it for it in contacts}
    assert titles["정보보호팀"]["fields"]["contact"] == "내선 4567"
    assert titles["재무팀"]["fields"]["contact"] == "내선 2345"  # 재분석해도 인계자 답변은 유지
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
