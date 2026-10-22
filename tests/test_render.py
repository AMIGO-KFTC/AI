from datetime import date

from amigo_agent import render_document
from amigo_agent.periods import plan_months
from amigo_agent.state import empty_slots

from htmltables import html_tables, texts

CITE = {"source_id": "s1", "label": "업무정의서.pdf p.2", "chunk_id": "c1", "quote": "재무팀과 협의한다"}
TODAY = date(2026, 10, 4)


def render(slots, gaps=None, profile=None):
    return render_document(profile=profile or {"name": "김민수", "position": "과장"}, slots=slots, gaps=gaps or [], sources=[{"name": "업무정의서.pdf", "type": "pdf"}], version=3, today=TODAY)


def project(slots, name="홈페이지 리뉴얼", **fields):
    slots["irregular"]["items"].append({"id": f"irregular-{name}", "title": name, "fields": {"name": name, **fields}, "citations": [CITE]})


def collab(slots, party, project_name, **fields):
    slots["stakeholders"]["items"].append(
        {"id": f"stakeholders-{party}", "title": party, "fields": {"party": party, "project": project_name, **fields}, "citations": [CITE]}
    )


def test_render_numbers_citations_and_escapes_cells():
    slots = empty_slots()
    slots["systems"]["items"] = [
        {"id": "systems-1", "title": "CMS", "fields": {"name": "CMS", "purpose": "게시물 | 승인", "how_to_get": "전자결재\n신청"}, "citations": [CITE]},
        {"id": "systems-2", "title": "GA", "fields": {"name": "GA"}, "citations": [CITE]},
    ]
    gaps = [{"id": "g1", "slot": "systems", "description": "GA 신청 방법 없음", "status": "skipped"}]
    md = render(slots, gaps)
    assert "| 인계자 | 김민수 과장 |" in md
    assert "2026-10-04 · AMIGO 자동 작성 초안 v3" in md
    assert "| CMS | 게시물 \\| 승인 | - | 전자결재 / 신청 | ID: ________ / PW: ________ | [1] |" in md
    assert "| GA | - | - | - | ID: ________ / PW: ________ | [1] |" in md  # 같은 근거는 같은 번호
    assert '1. 업무정의서.pdf p.2 — "재무팀과 협의한다"' in md
    assert "- [시스템 접근 권한] GA 신청 방법 없음 (인계자 미확인)" in md
    assert "## 1. 업무 단위 요약 + 일정 명세표\n\n확인된 내용이 없습니다." in md


def test_unit_summary_draws_connected_bars_without_evidence_column():
    slots = empty_slots()
    slots["overview"]["items"] = [
        {"id": "o1", "title": "공동망 운영", "fields": {"name": "공동망 운영", "period": "3월~5월", "note": "상시 점검"}, "citations": [CITE]},
        {"id": "o2", "title": "40년사 발간 추진", "fields": {"name": "40년사 발간 추진", "period": "2026.10 ~ 2027.01"}, "citations": [CITE]},
        {"id": "o3", "title": "통계시스템", "fields": {"name": "통계시스템", "period": "미정"}, "citations": [CITE]},
        {"id": "o4", "title": "정기 점검", "fields": {"name": "정기 점검", "period": "1·4·7·10월"}, "citations": [CITE]},
    ]
    md = render(slots)
    (table,) = [t for t in html_tables(md) if t and texts(t[0])[:2] == ["단위업무명", "1월"]]
    assert texts(table[0]) == ["단위업무명"] + [f"{m}월" for m in range(1, 13)] + ["비고"]  # 근거 열이 없다
    rows = {r[0]["text"]: r for r in table[1:]}

    def bars(name):  # (시작 월, 이어진 달 수) 목록 — 막대 칸 하나가 연속된 달 전체를 차지한다
        out, month = [], 1
        for cell in rows[name][1:-1]:
            if "bar" in cell["cls"]:
                out.append((month, cell["colspan"]))
            month += cell["colspan"]
        return out

    assert bars("공동망 운영") == [(3, 3)]  # 3·4·5월이 끊김 없이 한 막대
    assert bars("40년사 발간 추진") == [(1, 1), (10, 3)]  # 10~12월은 한 막대, 해를 넘긴 1월은 따로
    assert bars("통계시스템") == [] and rows["통계시스템"][-1]["text"] == "계획기간: 미정"  # 월로 읽을 수 없으면 원문을 비고에
    assert bars("정기 점검") == [(1, 1), (4, 1), (7, 1), (10, 1)]  # 떨어진 달은 이어 붙이지 않는다
    assert all(sum(c["colspan"] for c in r) == 14 for r in table)  # 어느 행이든 14칸(이름·12개월·비고)
    assert rows["공동망 운영"][-1]["text"] == "상시 점검" and "[1]" not in md.split("## 2.")[0]


def test_collaboration_is_a_split_cell_inside_each_project_card():
    slots = empty_slots()
    project(slots, "홈페이지 리뉴얼", overview="노후 개편", progress="RFP 작성 중", decisions="CMS 유지")
    project(slots, "개인정보처리방침 개정", overview="법 개정", progress="검토 중")
    collab(slots, "재무팀 박지훈 차장", "홈페이지 리뉴얼", discussed="예산 3억 원", upcoming="이사회 승인 후 계약")
    collab(slots, "IT지원팀 최영호 과장", "홈페이지 리뉴얼", discussed="계정 발급")
    collab(slots, "법무팀", "개인정보처리방침 개정", discussed="문구 검토")
    collab(slots, "감사팀", "없는 프로젝트", discussed="사전 점검")
    md = render(slots)
    first, second = [t for t in html_tables(md) if t and texts(t[0]) == ["항목", "내용"]]
    labels = [r[0]["text"] for r in first[1:] if r[0]["tag"] == "th"]
    # 협업관계 칸은 '추진 경과 및 현황' 바로 뒤, 의사결정 앞에 있다
    assert labels.index("주요 협업관계 및 협의사항") == labels.index("추진 경과 및 현황") + 1
    assert labels.index("주요 의사결정 및 판단 근거") == labels.index("주요 협업관계 및 협의사항") + 1
    head = next(r for r in first if r[0]["text"] == "주요 협업관계 및 협의사항")
    assert head[0]["rowspan"] == 3  # 머리글 줄 + 담당자 2명 = 3줄에 걸쳐 칸이 나뉜다(왼쪽 라벨은 합쳐진 한 칸)
    assert texts(head) == ["주요 협업관계 및 협의사항", "담당자", "협의내용", "추후 고려사항"]
    i = first.index(head)
    assert texts(first[i + 1]) == ["재무팀 박지훈 차장", "예산 3억 원", "이사회 승인 후 계약"]
    assert texts(first[i + 2]) == ["IT지원팀 최영호 과장", "계정 발급", "-"]
    # 다른 프로젝트의 협업관계는 섞이지 않는다
    second_head = next(r for r in second if r[0]["text"] == "주요 협업관계 및 협의사항")
    assert second_head[0]["rowspan"] == 2 and texts(second[second.index(second_head) + 1]) == ["법무팀", "문구 검토", "-"]
    # 프로젝트를 맞출 수 없는 항목은 양식에 없는 표로 싣지 않고 '확인 필요 사항'에 올린다
    assert "연결되는" not in md and "| 감사팀 | 없는 프로젝트 | 사전 점검 | - |" not in md
    assert "'감사팀'의 주요 협업관계 및 협의사항이(가) 어느 프로젝트명에 해당하는지 확인이 필요합니다 (입력된 프로젝트명: '없는 프로젝트', 협의내용: 사전 점검) (인계자 확인 필요)" in md
    # 별도 장·그림·삭제한 열은 없다
    assert "비정기 업무 협업관계 및 협의사항" not in "\n".join(line for line in md.splitlines() if line.startswith("## "))
    assert "```mermaid" not in md and "관계 유형" not in md and "연락처" not in md and "협의 상대" not in md


def test_user_text_is_escaped_inside_html_tables():
    slots = empty_slots()
    project(slots, "리뉴얼", overview="<script>alert(1)</script>", progress="a & b")
    collab(slots, "<b>재무팀</b>", "리뉴얼", discussed='"예산" <img src=x onerror=alert(1)>')
    md = render(slots)
    assert "<script>" not in md and "<img" not in md and "<b>" not in md
    assert "&lt;script&gt;" in md and "a &amp; b" in md and "&lt;b&gt;재무팀&lt;/b&gt;" in md


def test_project_without_collaboration_shows_an_empty_row():
    slots = empty_slots()
    project(slots, "홈페이지 리뉴얼", progress="진행 중")
    (table,) = [t for t in html_tables(render(slots)) if t and texts(t[0]) == ["항목", "내용"]]
    row = next(r for r in table if r[0]["text"] == "주요 협업관계 및 협의사항")
    assert texts(row) == ["주요 협업관계 및 협의사항", "-"] and row[1]["colspan"] == 3


def test_collaboration_without_projects_is_still_shown():
    slots = empty_slots()
    collab(slots, "재무팀", "홈페이지 리뉴얼", discussed="예산 협의")
    md = render(slots)
    assert "확인된 프로젝트가 없습니다" in md and "| 재무팀 | 홈페이지 리뉴얼 | 예산 협의 | - |" not in md  # 양식에 없는 표로 싣지 않는다
    tail = md[md.index("확인 필요 사항"):]
    assert "'재무팀'의 주요 협업관계 및 협의사항이(가) 어느 프로젝트명에 해당하는지 확인이 필요합니다" in tail and "홈페이지 리뉴얼" in tail and "예산 협의" in tail  # 내용은 확인 필요 사항에 남는다


def test_leave_version_uses_the_same_designs():
    profile = {"name": "김민수", "mode": "leave"}
    slots = empty_slots(["leave_units", "leave_tasks", "systems"])
    slots["leave_units"]["items"] = [{"id": "u1", "title": "공동망 운영", "fields": {"name": "공동망 운영", "period": "11월~12월"}, "citations": [CITE]}]
    slots["leave_tasks"]["items"] = [
        {"id": "t1", "title": "차액 정산", "fields": {"name": "차액 정산", "overview": "정산", "schedule": "매월 말", "emergency": "재무팀 박지훈 차장(내선 2345)에게 문의"}, "citations": [CITE]}
    ]
    md = render(slots, profile=profile)
    gantt = [t for t in html_tables(md) if t][0]
    assert texts(gantt[0])[:2] == ["단위업무명", "1월"] and any("bar" in c["cls"] and c["colspan"] == 2 for c in gantt[1])  # 11~12월이 한 막대
    assert "| 비상 대응 및 협조사항 | 재무팀 박지훈 차장(내선 2345)에게 문의 |" in md  # 템플릿의 5번 항목이 카드의 한 행이다
    rows = [line.split("|")[1].strip() for line in md.splitlines() if line.startswith("| ")]
    assert rows.index("비상 대응 및 협조사항") == rows.index("중점관리사항") + 1  # 템플릿의 5번 항목 자리
    assert "## 1. 부재 중 처리할 단위 업무" in md and "## 2. 세부 업무" in md and "## 3. 시스템 접근 권한" in md


def test_plan_months_reads_common_period_notations():
    assert plan_months("2026.03~2026.12") == list(range(3, 13))
    assert plan_months("3월부터 12월까지") == list(range(3, 13))
    assert plan_months("1·4·7·10월") == [1, 4, 7, 10]
    assert plan_months("상시(연중)") == list(range(1, 13))
    assert plan_months("12월~2월") == [1, 2, 12]
    assert plan_months("2026.10 ~ 2027.01") == [1, 10, 11, 12]
    assert plan_months("") == [] and plan_months("미정") == []
