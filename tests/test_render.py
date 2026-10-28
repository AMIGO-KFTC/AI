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


def collab(slots, name, project_name, **fields):
    slots["stakeholders"]["items"].append(
        {"id": f"stakeholders-{name}", "title": name, "fields": {"name": name, "project": project_name, **fields}, "citations": [CITE]}
    )


def stage(slots, project_name, when, content, status, note=""):
    slots["milestones"]["items"].append(
        {"id": f"milestones-{content}", "title": content, "fields": {"when": when, "content": content, "note": note, "status": status, "project": project_name}}
    )


def test_render_marks_citations_next_to_the_text_and_leaves_out_pending_and_evidence_sections():
    slots = empty_slots()
    slots["systems"]["items"] = [
        {"id": "systems-1", "title": "CMS", "fields": {"name": "CMS", "purpose": "게시물 | 승인", "how_to_get": "전자결재\n신청"}, "citations": [CITE]},
        {"id": "systems-2", "title": "GA", "fields": {"name": "GA"}, "citations": [CITE]},
    ]
    gaps = [{"id": "g1", "slot": "systems", "description": "GA 신청 방법 없음", "status": "skipped"}]
    md = render(slots, gaps)
    assert "| 인계자 | 김민수 과장 |" in md
    assert "2026-10-04 · AMIGO 자동 작성 초안 v3" in md
    # 근거 문장과 겹치는 칸이 없으면 항목 이름 뒤에 번호를 단다. 같은 근거는 같은 번호
    assert "| CMS [1] | 게시물 \\| 승인 | - | 전자결재<br>신청 | ID: ________ / PW: ________ |" in md
    assert "| GA [1] | - | - | - | ID: ________ / PW: ________ |" in md
    # 양식에 없는 장(확인 필요 사항·근거 자료)과 근거 열은 문서에 싣지 않는다(화면의 오른쪽 패널·번호 팝업에서 본다)
    assert "확인 필요 사항" not in md and "근거 자료" not in md and "| 근거 |" not in md and "GA 신청 방법 없음" not in md
    # 일정 명세표 위에는 화면에서만 보이는 회색 작성 안내(doc-guide)가 붙는다
    assert '## 1. 업무 단위별 일정 명세표\n\n<span class="doc-guide">실제 업무 추진 현황 및 예상일정을 막대 그래프로 나타내주세요</span>\n\n확인된 내용이 없습니다.' in md


def test_unit_summary_draws_connected_bars_without_evidence_column():
    slots = empty_slots()
    slots["overview"]["items"] = [
        {"id": "o1", "title": "공동망 운영", "fields": {"name": "공동망 운영", "period": "3월~5월", "note": "상시 점검"}, "citations": [CITE]},
        {"id": "o2", "title": "40년사 발간 추진", "fields": {"name": "40년사 발간 추진", "period": "2026.10 ~ 2026.12"}, "citations": [CITE]},
        {"id": "o3", "title": "통계시스템", "fields": {"name": "통계시스템", "period": "미정"}, "citations": [CITE]},
        {"id": "o4", "title": "정기 점검", "fields": {"name": "정기 점검", "period": "1·4·7·10월"}, "citations": [CITE]},
    ]
    md = render(slots)
    (table,) = [t for t in html_tables(md) if t and texts(t[0]) == ["단위업무명", "계획기간", "비고"]]
    # 머리글 2행: '단위업무명 | 계획기간(1~12월 위로 병합) | 진행상황 요약' 아래에 1~12월. 근거 열이 없다
    assert [c["colspan"] for c in table[0]] == [1, 12, 1] and texts(table[1]) == [f"{m}월" for m in range(1, 13)]
    rows = {r[0]["text"]: r for r in table[2:]}

    def bars(name):  # (시작 월, 이어진 달 수) 목록 — 막대 칸 하나가 연속된 달 전체를 차지한다
        out, month = [], 1
        for cell in rows[name][1:-1]:
            if "bar" in cell["cls"]:
                out.append((month, cell["colspan"]))
            month += cell["colspan"]
        return out

    assert bars("공동망 운영") == [(3, 3)]  # 3·4·5월이 끊김 없이 한 막대
    assert bars("40년사 발간 추진") == [(10, 3)]  # 10~12월은 한 막대(해를 넘기는 표는 test_periods 에서 본다)
    assert bars("통계시스템") == [] and rows["통계시스템"][-1]["text"] == "계획기간: 미정"  # 월로 읽을 수 없으면 원문을 마지막 열(진행상황 요약)에
    assert bars("정기 점검") == [(1, 1), (4, 1), (7, 1), (10, 1)]  # 떨어진 달은 이어 붙이지 않는다
    assert all(sum(c["colspan"] for c in r) == 14 for r in table[2:]) and sum(c["colspan"] for c in table[0]) == 14  # 어느 행이든 14칸(이름·12개월·진행상황 요약)
    assert rows["공동망 운영"][-1]["text"] == "상시 점검" and "[1]" not in md.split("## 2.")[0]


def test_progress_and_collaboration_are_tables_inside_each_project_card():
    slots = empty_slots()
    project(slots, "홈페이지 리뉴얼", overview="노후 개편", decisions="CMS 유지")
    project(slots, "개인정보처리방침 개정", overview="법 개정")
    stage(slots, "홈페이지 리뉴얼", "2026.03", "기본계획 수립", "완료")
    stage(slots, "홈페이지 리뉴얼", "2026.10~", "RFP 작성", "진행", note="80%")
    collab(slots, "박지훈 차장", "홈페이지 리뉴얼", org="재무팀", role="예산 3억 원 협의", note="이사회 승인 후 계약", scope="내부")
    collab(slots, "정다은 PM", "홈페이지 리뉴얼", org="(주)한빛웹솔루션", role="구축 업체", phone="02-555-0182", scope="외부")
    collab(slots, "법무팀", "개인정보처리방침 개정", org="법무팀", role="문구 검토", scope="내부")
    collab(slots, "감사팀", "없는 프로젝트", org="감사팀", role="사전 점검")
    md = render(slots)
    first, second = [t for t in html_tables(md) if t and texts(t[0]) == ["항목", "내용"]]
    labels = [r[0]["text"] for r in first[1:] if r[0]["tag"] == "th" and r[0].get("rowspan", 1) >= 1]
    # 노션 C 순서: 1 프로젝트 개요 → 2 추진 경과(표) → 3 주요 협업관계(표) → 4 주요 의사결정 → 5 기타(중점 관리사항)
    order = [l for l in labels if l in ("프로젝트 개요", "추진 경과", "주요 협업관계", "주요 의사결정 및 판단 근거", "기타(중점 관리사항)")]
    assert order == ["프로젝트 개요", "추진 경과", "주요 협업관계", "주요 의사결정 및 판단 근거", "기타(중점 관리사항)"]
    progress = next(r for r in first if r[0]["text"] == "추진 경과")
    assert texts(progress) == ["추진 경과", "시기", "주요 추진 내용", "비고"] and progress[0]["rowspan"] == 3  # 상태(완료/진행)는 싣지 않는다
    i = first.index(progress)
    assert texts(first[i + 1]) == ["2026.03", "기본계획 수립", "-"] and texts(first[i + 2]) == ["2026.10~", "RFP 작성", "80%"]
    head = next(r for r in first if r[0]["text"] == "주요 협업관계")
    j = first.index(head)
    # 외부(소속 | 이름 | 주요역할 | 연락처 | 비고) 먼저, 내부(연락처 없음) 다음. 왼쪽 라벨은 합쳐진 한 칸
    assert texts(head) == ["주요 협업관계", "외부"] and head[0]["rowspan"] == 6
    assert texts(first[j + 1]) == ["소속", "이름", "주요역할", "연락처", "비고(유의사항)"]
    assert texts(first[j + 2]) == ["(주)한빛웹솔루션", "정다은 PM", "구축 업체", "02-555-0182", "-"]
    assert texts(first[j + 3]) == ["내부"] and texts(first[j + 4]) == ["소속", "이름", "주요역할", "비고(유의사항)"]
    assert texts(first[j + 5]) == ["재무팀", "박지훈 차장", "예산 3억 원 협의", "이사회 승인 후 계약"]
    # 다른 프로젝트의 행은 섞이지 않고, 경과가 없으면 빈 줄
    assert texts(next(r for r in second if r[0]["text"] == "추진 경과")) == ["추진 경과", "-"]
    second_head = next(r for r in second if r[0]["text"] == "주요 협업관계")
    assert texts(second_head) == ["주요 협업관계", "내부"] and texts(second[second.index(second_head) + 2]) == ["법무팀", "법무팀", "문구 검토", "-"]
    # 프로젝트를 맞출 수 없는 행은 양식에 없는 표로 싣지 않는다
    assert "감사팀" not in md
    assert "비정기 업무 주요 협업관계" not in "\n".join(line for line in md.splitlines() if line.startswith("## "))


def test_user_text_is_escaped_inside_html_tables():
    slots = empty_slots()
    project(slots, "리뉴얼", overview="<script>alert(1)</script>")
    stage(slots, "리뉴얼", "2026.01", "a & b", "완료")
    collab(slots, "<b>재무팀</b>", "리뉴얼", role='"예산" <img src=x onerror=alert(1)>')
    md = render(slots)
    assert "<script>" not in md and "<img" not in md and "<b>" not in md
    assert "&lt;script&gt;" in md and "a &amp; b" in md and "&lt;b&gt;재무팀&lt;/b&gt;" in md


def test_project_without_collaboration_shows_an_empty_row():
    slots = empty_slots()
    project(slots, "홈페이지 리뉴얼")
    (table,) = [t for t in html_tables(render(slots)) if t and texts(t[0]) == ["항목", "내용"]]
    row = next(r for r in table if r[0]["text"] == "주요 협업관계")
    assert texts(row) == ["주요 협업관계", "-"] and row[1]["colspan"] == 5


def test_rows_without_projects_are_not_shown():
    slots = empty_slots()
    collab(slots, "재무팀", "홈페이지 리뉴얼", role="예산 협의")
    md = render(slots)
    assert "#### 2) 비정기 업무\n\n확인된 내용이 없습니다." in md and "예산 협의" not in md  # 양식에 없는 표로 싣지 않는다


def test_leave_version_follows_template_a_b_c():
    profile = {"name": "김민수", "mode": "leave"}
    slots = empty_slots(["leave_tasks", "systems", "contacts"])
    slots["leave_tasks"]["items"] = [
        {"id": "t1", "title": "차액 정산", "fields": {"name": "차액 정산", "task_type": "정기", "schedule": "매월 말", "cautions": "재무팀 박지훈 차장(내선 2345)에게 문의"}, "citations": [CITE]}
    ]
    slots["contacts"]["items"] = [
        {"id": "c1", "title": "박지훈 차장", "fields": {"name": "박지훈 차장", "org": "재무팀", "role": "대금 지급", "phone": "내선 2345", "scope": "내부"}, "citations": [CITE]},
        {"id": "c2", "title": "정다은 PM", "fields": {"name": "정다은 PM", "org": "(주)한빛웹솔루션", "role": "유지보수", "phone": "02-555-0182"}, "citations": [CITE]},
    ]
    md = render(slots, profile=profile)
    assert "<th>1월</th>" not in md  # 장기휴가에는 월별 일정표가 없다
    assert "| 주의사항 및 후속조치 | 재무팀 박지훈 차장(내선 2345)에게 문의 [1] |" in md  # 근거 문장과 겹치는 칸 뒤에 번호
    rows = [line.split("|")[1].strip() for line in md.splitlines() if line.startswith("| ")]
    assert rows.index("주의사항 및 후속조치") == rows.index("업무 처리방법") + 1  # 템플릿 5번 항목 자리
    assert "## 1. 세부 업무" in md and "## 2. 주요 연락망" in md and "## 3. 기타 자료" in md and "### 시스템 접근 권한" in md
    # 주요 연락망은 인사발령 협업관계와 같은 형식: 외부(연락처 있음) / 내부(연락처 없음)
    contacts = md.split("## 2. 주요 연락망")[1].split("## 3.")[0]
    assert "**외부**\n\n| 소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항) |" in contacts
    assert "**내부**\n\n| 소속 | 이름 | 주요역할 | 비고(유의사항) |" in contacts
    assert contacts.index("**외부**") < contacts.index("**내부**") and "내선 2345" not in contacts  # 내부 표에는 연락처 열이 없다


def test_system_access_is_left_blank_for_handwriting_when_missing():
    md = render(empty_slots())
    part = md.split("## 3. 기타 자료")[1]
    guide = '<span class="doc-guide">시스템 접근 권한이 있을 경우 해당 내용을 작성해주세요.</span>'  # 화면에서만 보이는 회색 안내
    assert part.startswith(f"\n\n### 시스템 접근 권한\n\n{guide}\n\n| 시스템 |") and "AMIGO에게 요청" not in part
    assert "| - | - | - | - | - |" in part and "### 부서별 추가 자료" in part


def test_plan_months_reads_common_period_notations():
    assert plan_months("2026.03~2026.12") == list(range(3, 13))
    assert plan_months("3월부터 12월까지") == list(range(3, 13))
    assert plan_months("1·4·7·10월") == [1, 4, 7, 10]
    assert plan_months("상시(연중)") == list(range(1, 13))
    assert plan_months("12월~2월") == [1, 2, 12]
    assert plan_months("2026.10 ~ 2027.01") == [1, 10, 11, 12]
    assert plan_months("") == [] and plan_months("미정") == []


def test_regular_task_collaboration_is_split_into_external_and_internal_tables_inside_each_card():
    slots = empty_slots()
    slots["regular"]["items"] = [{"id": "r1", "title": "차액 정산", "fields": {"name": "차액 정산", "unit": "공동망 운영", "overview": "월말 정산", "decisions": "익월 5일 마감"}, "citations": [CITE]}]
    slots["partners"]["items"] = [
        {"id": "p1", "title": "박지훈 차장", "fields": {"org": "재무팀", "name": "박지훈 차장", "role": "회계자료 확인", "phone": "내선 2345", "note": "확정 전 수치 변경 확인", "scope": "내부", "task": "차액 정산"}},
        {"id": "p2", "title": "김하늘", "fields": {"org": "각 사업부서", "name": "김하늘", "role": "실적자료 취합", "note": "기한 전 사전 안내", "scope": "내부", "task": "차액 정산"}},
    ]
    md = render(slots)
    (card,) = [t for t in html_tables(md) if t and texts(t[0]) == ["항목", "내용"]]
    labels = [texts(r)[0] for r in card]
    at = labels.index("주요 협업관계")
    assert labels[at - 1] == "주요 의사결정 및 판단 근거"  # 의사결정 다음 자리(템플릿 순서)
    assert texts(card[at]) == ["주요 협업관계", "내부"] and card[at][0]["rowspan"] == 4  # 내부만 있으면 외부 표는 없다
    assert texts(card[at + 1]) == ["소속", "이름", "주요역할", "비고(유의사항)"]
    assert texts(card[at + 2]) == ["재무팀", "박지훈 차장", "회계자료 확인", "확정 전 수치 변경 확인"]
    assert "공동망 운영" not in md  # 단위업무명은 내부 값
    assert all(sum(c["colspan"] for c in r) == 6 for r in card if texts(r)[0] in ("업무 개요", "수행 시기"))  # 다른 행은 내용 칸을 5칸 병합


def test_citation_number_goes_right_after_the_field_it_supports_and_internal_ids_are_removed():
    slots = empty_slots()
    cite = {"source_id": "s1", "label": "매뉴얼.hwpx", "chunk_id": "c9", "quote": "게시물은 반드시 부서장 승인 후 게시한다"}
    slots["regular"]["items"] = [{"id": "r1", "title": "게시물 점검", "citations": [cite],
        "fields": {"name": "게시물 점검", "overview": "주간 점검(E10)", "key_points": "부서장 승인 후 게시해야 합니다"}}]
    md = render(slots)
    assert '<th>중점 관리사항</th><td colspan="5">부서장 승인 후 게시해야 합니다 [1]</td>' in md and "##### 가. 게시물 점검\n" in md
    assert '<th>업무 개요</th><td colspan="5">주간 점검</td>' in md and "(E10)" not in md


def test_main_contents_group_detail_tasks_under_their_unit():
    """노션 '추가 요구사항 정의' 16: 2. 주요 내용 → A. 단위업무 → 1) 정기업무 / 2) 비정기 업무 → 가. 세부업무."""
    profile = {"name": "김민수", "units": [{"name": "자금세탁방지제도 운영", "regular": ["위험평가"], "irregular": ["모니터링 개선 기획"]}, {"name": "공동망 운영", "regular": ["장애 대응"], "irregular": []}]}
    slots = empty_slots()
    slots["regular"]["items"] = [
        {"id": "r1", "title": "위험평가", "fields": {"name": "위험평가", "unit": "자금세탁방지제도 운영"}, "citations": []},
        {"id": "r2", "title": "장애 대응", "fields": {"name": "장애 대응", "unit": "공동망 운영"}, "citations": []},
    ]
    slots["irregular"]["items"] = [{"id": "p1", "title": "모니터링 개선 기획", "fields": {"name": "모니터링 개선 기획", "unit": "자금세탁방지제도 운영"}, "citations": []}]
    md = render_document(profile=profile, slots=slots, gaps=[], sources=[], version=1, today=date(2026, 10, 28))
    heads = [line for line in md.splitlines() if line.startswith("#")][1:]
    assert heads[:9] == [
        "## 1. 업무 단위별 일정 명세표", "## 2. 주요 내용",
        "### A. 자금세탁방지제도 운영", "#### 1) 정기업무", "##### 가. 위험평가", "#### 2) 비정기 업무", "##### 가. 모니터링 개선 기획",
        "### B. 공동망 운영", "#### 1) 정기업무",
    ]
    assert "##### 가. 장애 대응" in md and "## 3. 기타 자료" in md


def test_sentences_in_a_cell_are_split_by_line_breaks():
    """노션 '추가 요구사항 정의': 새 문장은 ' / ' 대신 줄바꿈으로(표 칸 안에서는 <br>). 월별 일정표 칸은 그대로 둔다."""
    slots = empty_slots()
    slots["regular"]["items"] = [{"id": "r1", "title": "보고", "fields": {"name": "보고", "procedure": "1. 요청 / 2. 검토\n3. 보고"}, "citations": []}]
    slots["overview"]["items"] = [{"id": "o1", "title": "공동망", "fields": {"name": "공동망", "period": "3월~5월", "note": "A 완료 / B 예정"}}]
    md = render(slots)
    assert "1. 요청<br>2. 검토<br>3. 보고" in md and " / 2. 검토" not in md
    assert "<td>A 완료 / B 예정</td></tr>" in md


def test_single_numbered_item_drops_its_number():
    """노션 '추가 요구사항 정의' 17: 번호 목록 항목이 하나뿐이면 '1.'을 붙이지 않는다(둘 이상이면 그대로)."""
    slots = empty_slots()
    slots["regular"]["items"] = [
        {"id": "r1", "title": "위험평가", "fields": {"name": "위험평가", "key_points": "1. 수검부서와의 협의 과정 운영: 자율성 존중", "procedure": "1. 요청 / 2. 검토"}, "citations": []}
    ]
    md = render(slots)
    assert "<th>중점 관리사항</th><td colspan=\"5\">수검부서와의 협의 과정 운영: 자율성 존중</td>" in md and "1. 요청<br>2. 검토" in md


def test_team_lead_leave_uses_the_staff_form():
    """관리자용 6: 팀장의 장기휴가는 실무자와 같은 양식(세부 업무 · 주요 연락망 · 기타 자료)."""
    from amigo_agent.template import slots_for

    assert [s.key for s in slots_for({"form": "office", "mode": "leave"})] == ["leave_tasks", "contacts", "systems"]
    md = render_document(profile={"form": "office", "mode": "leave"}, slots={}, gaps=[], sources=[], version=1, today=date(2026, 10, 28))
    assert "## 1. 세부 업무" in md and "원내 양식" not in md
