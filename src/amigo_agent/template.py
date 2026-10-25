"""인수인계서 양식(슬롯) 정의.

노션 '인수인계서 형식' 페이지의 최종 템플릿을 따른다. 인수인계서는 두 가지 버전이며, 버전은 인계자 정보의 `mode` 로 고른다.

[인사발령]  A. 업무 단위 요약 + 일정 명세표(월별 막대) → B. 정기업무(협업관계는 각 업무 안) → C. 비정기 업무(프로젝트성, 협업관계는 각 프로젝트 안) →
            D. 시스템 접근 권한 → E. 부서별 추가 자료
[장기휴가]  A. 세부 업무 → B. 시스템 접근 권한 → C. 주요 연락망

항목 이름은 페이지의 항목을, `hint` 는 그 항목의 '핵심 질문'을 그대로 옮긴 것이다(질문 설계와 AI 지침에 쓰인다).
양식의 모든 항목은 필수다(빠지면 질문한다). 조건이 붙은 항목(장기휴가 '현재 진행 상태'는 프로젝트성 업무만)은
`required_when` 으로 조건이 맞을 때만 필수로 본다.

인수인계서의 각 장(章)을 '슬롯'으로 보고, 슬롯마다 채워야 할 필드·필수 필드·검색어·질문 예시를 정의한다.
STAGE 1(분석)은 슬롯별로 근거를 찾아 항목을 채우고, 필수 필드가 비어 있으면 '공백(gap)'으로 판정해
STAGE 3(Q&A)에서 한 번에 하나씩 질문한다. 사내 양식이 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_REASSIGNMENT = "reassignment"  # 인사발령
MODE_LEAVE = "leave"  # 장기휴가
MODE_LABELS = {MODE_REASSIGNMENT: "인사발령", MODE_LEAVE: "장기휴가"}


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    hint: str = ""


@dataclass(frozen=True)
class SlotSpec:
    key: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]
    required: tuple[str, ...]
    min_items: int
    queries: tuple[str, ...]
    keywords: tuple[str, ...]
    default_question: str
    modes: tuple[str, ...] = (MODE_REASSIGNMENT,)  # 이 장을 쓰는 인수인계서 버전
    layout: str = "table"  # 문서에서 항목을 표(table), 항목별 카드(card), 월별 막대 일정표(gantt)로 보일지
    handwritten: tuple[str, ...] = ()  # AI 가 채우지 않고 문서에 빈칸으로 남기는 열(예: ID/PW)
    evidence: bool = True  # 문서에 근거 번호([1] …)를 달고 근거 자료 목록에 올릴지
    # 이 장을 따로 싣지 않고 다른 장의 카드 안에 넣는다. embed_into=받는 장, embed_link=받는 장의 항목 이름과 맞출 필드,
    # embed_after=받는 장의 이 필드 행 뒤에 넣는다, embed_label=카드 안의 행 이름
    embed_into: str = ""
    embed_link: str = ""
    embed_after: str = ""
    embed_label: str = ""
    title_key: str = ""  # 항목 이름으로 쓸 필드(비우면 첫 필드). 표의 열 순서와 항목 이름 필드가 다를 때 쓴다
    # 묻지 않고 문서를 만들 때 수집된 내용으로 채우는 필드(업무 단위 요약의 진행상황 요약). 필수가 아니고 질문 대상이 아니다.
    auto: tuple[str, ...] = ()
    # 이 장에서 묻는 질문의 범위(정규식). 비어 있지 않으면 이 표현이 들어간 질문만 남긴다(업무 단위 요약은 일정만 묻는다).
    ask_scope: str = ""
    # 조건부 필수: (필드, 조건 필드, 조건 값들). 조건 필드 값에 조건 값 중 하나가 들어 있을 때만 그 필드를 필수로 본다.
    required_when: tuple[tuple[str, str, tuple[str, ...]], ...] = ()

    @property
    def leave(self) -> bool:
        """장기휴가 버전에도 들어가는 장인지(화면의 양식 안내에서 쓴다)."""
        return MODE_LEAVE in self.modes

    @property
    def title_field(self) -> str:
        return self.title_key or self.fields[0].key

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    def label(self, key: str) -> str:
        spec = self.field(key)
        return spec.label if spec else key


BOTH = (MODE_REASSIGNMENT, MODE_LEAVE)

# 슬롯 순서는 문서 순서다. 버전별로 걸러 쓰므로(slots_for) 두 버전의 장 순서가 각각 템플릿과 같다.
SLOTS: tuple[SlotSpec, ...] = (
    # ------------------------------------------------------------------ [인사발령] A
    SlotSpec(
        key="overview",
        title="업무 단위 요약 + 일정 명세표",
        description="인계하는 단위 업무(예: 공동망 운영, 40년사 발간 추진)와 업무별 계획기간(월별 일정). "
        "이 장의 질문은 단위업무명과 일정(언제 시작해 언제 끝나는지, 주기·마감)만 묻습니다. 진행상황·업무 내용은 묻지 않습니다(정기업무·비정기 업무 장에서 자세히 다룹니다). "
        "진행상황 요약은 문서를 만들 때 지금까지 수집된 내용으로 자동 작성합니다. "
        "자료에 나오는 인계자의 단위업무는 인계자가 입력한 단위업무 외의 것도 빠뜨리지 말고 모두 항목으로 만듭니다(업무분장표·계획서에 있는 업무 등). "
        "입력하지 않은 업무가 실제 인계 대상인지는 인계자에게 예/아니요로 따로 확인합니다.",
        fields=(
            FieldSpec("name", "단위업무명", "예: 공동망 운영"),
            FieldSpec("period", "계획기간", "예: 2026.03~2026.12, 매월 상시"),
            FieldSpec("note", "진행상황 요약", "단위 업무가 지금 어디까지 진행되었는가?(문서를 만들 때 수집된 내용으로 자동 요약, 질문하지 않음)"),
        ),
        required=("name", "period"),
        auto=("note",),
        ask_scope=r"기간|일정|언제|시기|시작|종료|끝나|마감|기한|주기|매일|매주|매월|매년|분기|상시|부터|까지|\d+\s*월|년도|연중|단위\s*업무|업무명",
        min_items=1,
        queries=("단위업무 업무 개요 계획 일정", "연간 계획 추진 일정 기간", "담당 업무 목록 업무 분장", "추진 현황 진행 상황"),
        keywords=("업무", "담당", "계획", "기간", "일정", "추진", "운영", "현황", "진행"),
        default_question="인계하실 단위 업무를 업무명과 계획기간(언제부터 언제까지, 또는 상시)과 함께 알려 주시겠어요?",
        layout="gantt",
        evidence=False,
    ),
    # ------------------------------------------------------------------ [인사발령] B
    SlotSpec(
        key="regular",
        title="정기업무",
        description="일·주·월·분기·연 단위로 정기성이 있는 세부 업무. 개요·시기·절차·중점 관리사항·의사결정 근거·향후 과제·참고자료를 담는다(주요 협업관계는 별도 장 partners 의 표).",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 차액 정산 수행"),
            FieldSpec("overview", "업무 개요", "무엇을, 왜 하는가?"),
            FieldSpec("timing", "수행 시기", "언제 하는가?"),
            FieldSpec("procedure", "수행 절차", "어떻게 하는가?"),
            FieldSpec("key_points", "중점 관리사항", "무엇을 중요하게 관리하고 주의해야 하는가?"),
            FieldSpec("decisions", "주요 의사결정 및 판단 근거", "무엇을, 왜 그렇게 결정했는가?"),
            FieldSpec("future", "향후 과제 및 발전 방향", "무엇을 이어가고 개선해야 하는가?"),
            FieldSpec("references", "참고자료 및 관련 근거", "어떤 자료와 규정을 참고해야 하는가?"),
        ),
        required=("name", "overview", "timing", "procedure", "key_points", "decisions", "future", "references"),
        min_items=1,
        queries=("매일 매주 매월 정기 반복 업무", "주기적 점검 보고 일정 기한", "연간 일정 분기 갱신 신청", "업무 절차 처리 방법"),
        keywords=("매일", "매주", "매월", "분기", "매년", "정기", "주기", "월간", "주간", "연간", "영업일", "절차"),
        default_question="정기적으로 반복하는 세부 업무(매일·매월·매년 등)가 있다면, 업무명과 수행 시기, 수행 절차를 알려 주시겠어요?",
        layout="card",
    ),
    SlotSpec(
        key="partners",
        title="정기업무 주요 협업관계",
        description="정기업무를 하면서 함께 일하는 부서·사람. 문서에서는 각 정기업무의 '주요 협업관계' 칸을 나눠 소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항) 표로 들어간다.",
        fields=(
            FieldSpec("org", "소속", "팀·부서·기관·업체. 예: 재무팀"),
            FieldSpec("name", "이름", "사람 이름(직급 포함 가능) 또는 팀·기관명만. 예: 박지훈 차장, 재무팀, (주)한빛웹솔루션. 업무명·시스템명은 쓰지 않는다"),
            FieldSpec("role", "주요역할", "이 업무에서 무엇을 함께 하는가?"),
            FieldSpec("phone", "연락처", "전화·내선·메일"),
            FieldSpec("note", "비고(유의사항)", "협업할 때 무엇을 주의해야 하는가?"),
            FieldSpec("task", "세부 업무명", "어느 정기업무에서의 협업인지"),
        ),
        required=("org", "name", "role", "phone", "note", "task"),
        min_items=1,
        queries=("정기 업무 협업 부서 담당자 연락처", "유관 부서 협조 요청 담당자", "업체 담당자 연락처 역할", "협업 시 유의사항"),
        keywords=("팀", "부서", "업체", "담당자", "협업", "협조", "연락처", "내선", "역할", "유의"),
        default_question="정기업무를 하면서 함께 일하는 부서나 담당자가 있다면, 어느 업무에서 누구(소속·이름)와 무엇을 하는지, 연락처와 협업할 때 주의할 점을 알려 주시겠어요?",
        evidence=False,
        embed_into="regular",
        embed_link="task",
        embed_after="decisions",
        embed_label="주요 협업관계",
        title_key="name",
    ),
    # ------------------------------------------------------------------ [인사발령] C
    SlotSpec(
        key="irregular",
        title="비정기 업무(프로젝트성)",
        description="프로젝트처럼 비정기로 수행하는 세부 업무. 개요·추진배경, 목표, 추진 경과, 협업관계 및 협의사항, 의사결정 근거, 중점 관리사항, 향후 추진과제를 담는다.",
        fields=(
            FieldSpec("name", "프로젝트명", "예: 은행 대리업 구축 프로젝트"),
            FieldSpec("overview", "프로젝트 개요", "무엇을, 왜 추진하는가?"),
            FieldSpec("goals", "프로젝트 목표", "무엇을 달성하려는가?"),
            FieldSpec("progress", "추진 경과", "지금까지 무엇을 했으며, 현재 어디까지 진행되었는가?"),
            FieldSpec("decisions", "주요 의사결정 및 판단 근거", "무엇을, 왜 그렇게 결정했는가?"),
            FieldSpec("key_points", "중점 관리사항", "무엇을 중요하게 관리하고 주의해야 하는가?"),
            FieldSpec("future", "향후 추진과제 및 발전 방향", "앞으로 무엇을 해야 하며, 어떤 방향으로 추진해야 하는가?"),
        ),
        required=("name", "overview", "goals", "progress", "decisions", "key_points", "future"),
        min_items=1,
        queries=("진행 중인 과제 프로젝트 현황", "추진 배경 목표 기대성과", "추진 경과 향후 과제 결정사항", "사업추진 계획"),
        keywords=("과제", "프로젝트", "추진", "진행", "예정", "사업", "입찰", "검토 중", "배경", "목표", "경과"),
        default_question="진행 중이거나 곧 시작될 프로젝트성 업무가 있다면, 추진 배경과 목표, 현재 진행 상황을 알려 주시겠어요?",
        layout="card",
    ),
    SlotSpec(
        key="stakeholders",
        title="비정기 업무 협업관계 및 협의사항",
        description="비정기 업무에서 누구와 무엇을 협의했고, 앞으로 어떤 협의가 필요한지. 문서에서는 각 프로젝트의 '주요 협업관계 및 협의사항' 칸을 나눠 담당자 | 협의내용 | 추후 고려사항 표로 들어간다.",
        fields=(
            FieldSpec("party", "담당자", "팀 또는 개인. 예: 재무팀, 재무팀 박지훈 차장"),
            FieldSpec("project", "프로젝트명", "어느 프로젝트에서의 협의인지"),
            FieldSpec("discussed", "협의내용", "누구와 무엇을 협의했는가?"),
            FieldSpec("upcoming", "추후 고려사항", "앞으로 어떤 협의가 필요한가?"),
        ),
        required=("party", "project", "discussed", "upcoming"),
        min_items=1,
        queries=("협업 부서 담당자 협의", "유관 부서 협의 요청 보고", "외부 기관 업체 담당자 협의", "일정 및 부서별 협조사항"),
        keywords=("팀", "부서", "업체", "담당자", "협의", "협업", "기관", "요청", "보고", "협조"),
        default_question="프로젝트를 하면서 협의한 팀이나 담당자가 있다면, 어느 프로젝트에서 누구와 무엇을 협의했고 앞으로 어떤 협의가 더 필요한지 알려 주시겠어요?",
        evidence=False,
        embed_into="irregular",
        embed_link="project",
        embed_after="progress",
        embed_label="주요 협업관계 및 협의사항",
    ),
    # ------------------------------------------------------------------ [장기휴가] A
    # ------------------------------------------------------------------ [장기휴가] B
    SlotSpec(
        key="leave_tasks",
        title="세부 업무",
        description="부재 기간 중 대신 처리할 세부 업무. 업무 유형, 일정·처리사항, (프로젝트성이면) 현재 진행 상태, 처리방법, 주의사항 및 후속조치, 참고자료를 담는다.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 차액 정산 수행"),
            FieldSpec("task_type", "업무 유형", "정기/수시/프로젝트성 중 하나"),
            FieldSpec("schedule", "주요 일정 및 처리사항", "부재 기간 중 언제, 무엇을 처리해야 하는가?"),
            FieldSpec("status", "현재 진행 상태", "(프로젝트성 업무만) 현재 진행단계는 어느 정도이며, 다음 진행단계는 어떻게 되는가?"),
            FieldSpec("method", "업무 처리방법", "어떻게 처리하면 되는가?"),
            FieldSpec("cautions", "주의사항 및 후속조치", "조심할 점, 문제 발생 시 대응, 아직 끝나지 않은 일이 있는가?"),
            FieldSpec("references", "참고자료", "업무에 참고할 수 있는 자료가 어떤 것이 있는가?"),
        ),
        required=("name", "task_type", "schedule", "status", "method", "cautions", "references"),
        required_when=(("status", "task_type", ("프로젝트",)),),
        min_items=1,
        modes=(MODE_LEAVE,),
        queries=("매일 매주 매월 정기 반복 업무", "처리 방법 절차 일정 마감", "프로젝트 진행 상태 다음 단계", "주의사항 비상 연락 미결 후속 조치"),
        keywords=("일정", "처리", "절차", "방법", "마감", "프로젝트", "진행", "주의", "비상", "미결", "후속", "매뉴얼"),
        default_question="휴가 중에 대신 처리해야 하는 세부 업무가 있다면, 업무명과 업무 유형(정기/수시/프로젝트성), 처리 일정과 처리 방법을 알려 주시겠어요?",
        layout="card",
    ),
    # ------------------------------------------------------------------ 두 버전 공통: 시스템 접근 권한([인사발령] D / [장기휴가] B)
    SlotSpec(
        key="systems",
        title="시스템 접근 권한",
        description="업무에 쓰는 시스템·도구, 용도, 필요한 권한 수준과 권한 신청·이관 방법. ID/PW 는 보안상 AI 에 입력하지 않고 문서에서 수기로 적는다.",
        fields=(
            FieldSpec("name", "시스템"),
            FieldSpec("purpose", "용도"),
            FieldSpec("access", "권한 수준", "관리자/편집자/조회 등"),
            FieldSpec("how_to_get", "신청·이관 방법"),
        ),
        required=("name", "purpose", "access", "how_to_get"),
        min_items=1,
        modes=BOTH,
        queries=("사용 시스템 접근 권한 계정", "권한 신청 방법 이관 승인", "관리자 계정 콘솔 접속"),
        keywords=("시스템", "권한", "계정", "콘솔", "관리자", "접속", "CMS", "로그인"),
        default_question="업무에 사용하는 시스템과, 인수자가 권한을 받으려면 어떻게 신청해야 하는지 알려 주시겠어요?\n(ID/PW 는 적지 마세요)",
        handwritten=("ID / PW (수기 기재)",),
    ),
    # ------------------------------------------------------------------ [인사발령] E
    SlotSpec(
        key="dept_notes",
        title="부서별 추가 자료",
        description="부서 업무의 특성상 반드시 챙겨야 하는 사항(외주 관리, 가맹점 관리, 라이선스 등)과 근거가 되는 사무인수인계서 사무목록·세부시행세칙.",
        fields=(
            FieldSpec("topic", "추가 자료", "예: 가맹점 관리, 외주 관리, 라이선스"),
            FieldSpec("detail", "관리 내용", "무엇을 어떻게 챙겨야 하는지"),
            FieldSpec("basis", "관련 규정·자료", "사무인수인계서 사무목록·세부시행세칙 등"),  # 문서 표의 근거 번호 열('근거')과 이름이 겹치지 않게
        ),
        required=("topic", "detail", "basis"),
        min_items=1,
        queries=("부서별 추가 자료 외주 관리 가맹점 관리", "라이선스 라이센스 관리 계약", "사무인수인계서 사무목록 세부시행세칙"),
        keywords=("외주", "가맹점", "라이선스", "라이센스", "위탁", "특이", "지침", "세칙", "사무목록"),
        default_question="소속 부서만의 추가 관리 사항(외주 관리, 가맹점 관리, 라이선스 등)이 있나요?\n없으면 '해당 없음'으로 건너뛰어 주세요.",
    ),
    # ------------------------------------------------------------------ [장기휴가] C
    SlotSpec(
        key="contacts",
        title="주요 연락망",
        description="부재 중 업무와 관련해 연락할 사람: 이름·기관·부서·직책·연락처·관련 업무.",
        fields=(
            FieldSpec("name", "이름", "예: 박지훈"),
            FieldSpec("org", "기관", "예: 금융결제원, (주)한빛웹솔루션"),
            FieldSpec("dept", "부서", "예: 재무팀"),
            FieldSpec("position", "직책", "예: 차장"),
            FieldSpec("phone", "연락처", "예: 내선 2345, 02-555-0182"),
            FieldSpec("task", "관련 업무", "어떤 업무로 연락하는가?"),
        ),
        required=("name", "org", "dept", "position", "phone", "task"),
        min_items=1,
        modes=(MODE_LEAVE,),
        queries=("담당자 연락처 내선 전화", "비상 연락망 문의처", "협업 부서 업체 담당자"),
        keywords=("연락", "연락처", "내선", "전화", "담당자", "문의", "메일"),
        default_question="휴가 중 업무와 관련해 연락할 사람이 있다면 이름·기관·부서·직책·연락처와 관련 업무를 알려 주시겠어요?",
    ),
)

SLOT_BY_KEY: dict[str, SlotSpec] = {s.key: s for s in SLOTS}
SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
ALL_FIELD_KEYS: tuple[str, ...] = tuple(dict.fromkeys(f.key for s in SLOTS for f in s.fields))


def mode_of(profile: dict[str, Any] | None) -> str:
    """인계자 정보의 `mode` 를 정규화한다(없거나 모르는 값이면 인사발령)."""
    mode = str((profile or {}).get("mode") or "").strip().lower()
    return MODE_LEAVE if mode == MODE_LEAVE else MODE_REASSIGNMENT


def leave_period(profile: dict[str, Any] | None) -> str:
    """장기휴가의 휴가 기간 문구("2026-11-02 ~ 2026-11-27"). 장기휴가가 아니거나 날짜가 없으면 빈 문자열."""
    if mode_of(profile) != MODE_LEAVE:
        return ""
    start = str((profile or {}).get("leave_start") or "").strip()
    end = str((profile or {}).get("leave_end") or "").strip()
    if start and end:
        return f"{start} ~ {end}"
    return start or end


def slots_for(profile: dict[str, Any] | None) -> tuple[SlotSpec, ...]:
    """이번 인수인계서에 쓰는 슬롯(버전별로 장 구성이 다르다)."""
    mode = mode_of(profile)
    return tuple(s for s in SLOTS if mode in s.modes)


COVERAGE_LABELS = {"sufficient": "충분", "partial": "부분", "missing": "부족"}

STAGES = (
    ("analyzing", 1, "자료 분석"),
    ("summary", 2, "분석 요약"),
    ("qna", 3, "질의응답"),
    ("composing", 4, "문서 생성"),
    ("review", 4, "검토"),
)
STAGE_NUMBER = {key: number for key, number, _ in STAGES}
STAGE_LABEL = {key: label for key, _, label in STAGES}
