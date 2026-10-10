from datetime import date

from amigo_agent import render_document
from amigo_agent.state import empty_slots


def _render(slots, gaps=(), profile=None, **kw):
    return render_document(
        profile=profile or {"name": "김민수", "position": "과장"},
        slots=slots,
        gaps=list(gaps),
        extras={"overview": "개요", "key_points": ["첫째"]},
        sources=[{"name": "업무정의서.pdf", "type": "pdf"}],
        version=3,
        today=date(2026, 10, 4),
        **kw,
    )


def test_render_numbers_citations_and_escapes_cells():
    slots = empty_slots()
    cite = {"source_id": "s1", "label": "업무정의서.pdf p.2", "chunk_id": "c1", "quote": "재무팀과 협의한다"}
    slots["stakeholders"]["items"] = [
        {"id": "s-1", "title": "재무팀", "fields": {"party": "재무팀", "person": "박지훈 | 차장", "role": "대금\n지급"}, "citations": [cite]},
        {"id": "s-2", "title": "IT지원팀", "fields": {"party": "IT지원팀"}, "citations": [cite]},
    ]
    gaps = [{"id": "g1", "slot": "stakeholders", "description": "IT지원팀 담당자 없음", "status": "skipped"}]
    md = _render(slots, gaps, {"name": "김민수", "position": "과장", "task_name": "CD공동망 운영"})
    assert "| 인계자 | 김민수 과장 |" in md
    assert "| 단위 업무명 | CD공동망 운영 |" in md and "| 인계 유형 | 인사발령 |" in md
    assert "2026-10-04 · AMIGO 자동 작성 초안 v3" in md
    assert "| 재무팀 | - | - | 대금 / 지급 | 박지훈 \| 차장 | - | - | [1] |" in md
    assert "| IT지원팀 | - | - | - | - | - | - | [1] |" in md  # 같은 근거는 같은 번호
    assert '1. 업무정의서.pdf p.2 — "재무팀과 협의한다"' in md
    assert "- [이해관계자] IT지원팀 담당자 없음 (인계자 미확인)" in md
    assert "## 1. 업무 소개 — CD공동망 운영" in md


def test_stakeholder_diagram_colors_relations_and_groups_sides():
    slots = empty_slots()
    slots["stakeholders"]["items"] = [
        {"id": "a", "title": "재무팀", "fields": {"party": "재무팀", "person": "박지훈 차장", "relation": "요청"}},
        {"id": "b", "title": "금융위원회", "fields": {"party": "금융위원회", "relation": "보고"}},
        {"id": "c", "title": "정보보호팀", "fields": {"party": "정보보호팀", "role": "정책을 협의한다"}},
    ]
    md = _render(slots, profile={"name": "김민수", "task_name": "CD공동망 운영", "organization": "금융플랫폼팀"})
    assert "```mermaid" in md and 'subgraph IN["내부"]' in md and 'subgraph OUT["외부"]' in md
    assert "ME -->|요청| S1" in md and "ME -->|보고| S2" in md and "ME <-->|협의| S3" in md
    assert "linkStyle 0 stroke:#2563eb" in md and "linkStyle 1 stroke:#ea580c" in md and "linkStyle 2 stroke:#16a34a" in md


def test_regular_and_irregular_tasks_render_per_item_blocks():
    slots = empty_slots()
    slots["recurring"]["items"] = [{"id": "r", "title": "차액 정산 수행", "fields": {"name": "차액 정산 수행", "cycle": "매월", "decisions": "수기 대사 폐지(오류 감소)"}}]
    slots["projects"]["items"] = [{"id": "p", "title": "은행 대리업 구축", "fields": {"name": "은행 대리업 구축", "background": "포용금융 지원을 위한 금융위 요청"}}]
    md = _render(slots)
    assert "### 3-1. 차액 정산 수행" in md and "| 주요 결정사항·근거 | 수기 대사 폐지(오류 감소) |" in md
    assert "### 4-1. 은행 대리업 구축" in md and "| 소개 및 배경 | 포용금융 지원을 위한 금융위 요청 |" in md


def test_accounts_table_leaves_id_pw_blank_for_handwriting():
    slots = empty_slots()
    slots["accounts"]["items"] = [{"id": "x", "title": "CMS", "fields": {"name": "CMS", "purpose": "게시물 관리", "how_to_get": "전자결재"}}]
    md = _render(slots)
    assert "| 시스템 | 용도 | 권한 수준 | 신청·이관 방법 | ID | PW | 근거 |" in md
    assert "| CMS | 게시물 관리 | - | 전자결재 | 　 | 　 | - |" in md
    assert "수기" not in md.split("## 5.")[0]  # 안내문은 계정 장에만 있다
    assert "직접 기재" in md


def test_leave_mode_is_light():
    from amigo_agent.template import slot_keys_for_mode

    assert slot_keys_for_mode("leave") == ("duties", "stakeholders", "recurring", "accounts")
    slots = empty_slots("leave")
    assert set(slots) == {"duties", "stakeholders", "recurring", "accounts"}
    md = _render(slots, profile={"name": "김민수", "mode": "leave"})
    assert "| 인계 유형 | 장기휴가 (Light) |" in md
    assert "비정기 업무" not in md and "부서별 특이사항" not in md
    assert "## 4. 시스템 접근 계정" in md and "## 5. 확인 필요 사항" in md
