from datetime import date

from amigo_agent import render_document
from amigo_agent.state import empty_slots


def test_render_numbers_citations_and_escapes_cells():
    slots = empty_slots()
    cite = {"source_id": "s1", "label": "업무정의서.pdf p.2", "chunk_id": "c1", "quote": "재무팀과 협의한다"}
    slots["stakeholders"]["items"] = [
        {"id": "stakeholders-1", "title": "재무팀", "fields": {"party": "재무팀", "person": "박지훈 | 차장", "detail": "대금\n지급"}, "citations": [cite]},
        {"id": "stakeholders-2", "title": "IT지원팀", "fields": {"party": "IT지원팀"}, "citations": [cite]},
    ]
    gaps = [{"id": "g1", "slot": "stakeholders", "description": "IT지원팀 담당자 없음", "status": "skipped"}]
    md = render_document(
        profile={"name": "김민수", "position": "과장"},
        slots=slots,
        gaps=gaps,
        extras={"overview": "개요", "key_points": ["첫째"]},
        sources=[{"name": "업무정의서.pdf", "type": "pdf"}],
        version=3,
        today=date(2026, 10, 4),
    )
    assert "| 인계자 | 김민수 과장 |" in md
    assert "2026-10-04 · AMIGO 자동 작성 초안 v3" in md
    assert "| 재무팀 | - | - | 박지훈 \\| 차장 | - | 대금 / 지급 | [1] |" in md
    assert "| IT지원팀 | - | - | - | - | - | [1] |" in md  # 같은 근거는 같은 번호
    assert '1. 업무정의서.pdf p.2 — "재무팀과 협의한다"' in md
    assert "- [이해관계자] IT지원팀 담당자 없음 (인계자 미확인)" in md
    assert "## 2. 업무 소개\n\n확인된 내용이 없습니다." in md
