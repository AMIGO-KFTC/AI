"""인수인계서 양식(슬롯) 정의.

2026-10-04 '아이디어 구체화 회의' 결정을 따른다. 업무 단위별로 작성하며(업무 소개 → 이해관계자 → 세부 업무),
세부 업무는 정기/비정기로 나눠 '주요 결정·협의사항(업무 히스토리)'과 '후임자에게 바라는 점'까지 담는다.
인수인계서는 인사발령(전체)과 장기휴가(Light) 두 가지 버전이 있다. 장기휴가 버전은 세부 업무·이해관계자·
시스템 접근 계정만 담는다(`SlotSpec.leave`). 버전은 인계자 정보의 `mode` 로 고른다.

인수인계서의 각 장(章)을 '슬롯'으로 보고, 슬롯마다 채워야 할 필드·필수 필드·검색어·질문 예시를 정의한다.
STAGE 1(분석)은 슬롯별로 근거를 찾아 항목을 채우고, 필수 필드가 비어 있으면 '공백(gap)'으로 판정해
STAGE 3(Q&A)에서 한 번에 하나씩 질문한다. 사내 양식이 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_REASSIGNMENT = "reassignment"  # 인사발령(전체 양식)
MODE_LEAVE = "leave"  # 장기휴가(Light)
MODE_LABELS = {MODE_REASSIGNMENT: "인사발령", MODE_LEAVE: "장기휴가(Light)"}


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
    leave: bool = False  # 장기휴가(Light) 버전에도 포함할지
    layout: str = "table"  # 문서에서 항목을 표(table)로 보일지, 항목별 카드(card)로 보일지
    handwritten: tuple[str, ...] = ()  # AI 가 채우지 않고 문서에 빈칸으로 남기는 열(예: ID/PW)

    @property
    def title_field(self) -> str:
        return self.fields[0].key

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    def label(self, key: str) -> str:
        spec = self.field(key)
        return spec.label if spec else key


SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        key="overview",
        title="업무 소개",
        description="단위 업무(예: CD공동망 운영)의 이름과 3줄 이내 소개. 업무지침·관계법령·업무매뉴얼을 명시한다.",
        fields=(
            FieldSpec("name", "단위 업무명", "예: CD공동망 운영"),
            FieldSpec("summary", "업무 소개", "3줄 이내로 어떤 업무인지"),
            FieldSpec("guideline", "업무지침", "근거가 되는 내규·지침명"),
            FieldSpec("law", "관계법령", "관련 법령·규정"),
            FieldSpec("manual", "업무매뉴얼", "매뉴얼·참고 문서명"),
        ),
        required=("name", "summary"),
        min_items=1,
        queries=("업무 개요 업무 소개 담당 업무", "업무지침 관계법령 업무매뉴얼", "업무 분장 역할 책임 범위"),
        keywords=("업무", "담당", "소개", "개요", "지침", "법령", "매뉴얼", "운영"),
        default_question="맡고 계신 단위 업무의 이름과, 어떤 업무인지 3줄 이내로 소개해 주시겠어요?",
        layout="card",
    ),
    SlotSpec(
        key="stakeholders",
        title="이해관계자",
        description="업무상 관계된 내부 부서와 외부 기관·업체, 관계 유형(요청·협의·보고 등)과 담당자·연락처, 최근 협의 내용.",
        fields=(
            FieldSpec("party", "부서·기관·업체"),
            FieldSpec("scope", "구분", "내부 / 외부"),
            FieldSpec("relation", "관계 유형", "요청 / 협의 / 보고 등"),
            FieldSpec("person", "담당자", "이름과 직위"),
            FieldSpec("contact", "연락처", "내선, 메일, 휴대폰 등"),
            FieldSpec("detail", "주요 협의 내용", "최근 나눈 이야기, 이슈"),
        ),
        required=("party", "scope", "relation", "person", "contact"),
        min_items=1,
        leave=True,
        queries=("협업 부서 담당자 연락처", "유관 부서 협의 요청 보고", "외부 기관 업체 담당자 계약"),
        keywords=("팀", "부서", "업체", "담당자", "연락", "협의", "협업", "내선", "기관", "요청", "보고"),
        default_question="업무상 자주 주고받는 내부 부서나 외부 기관·업체가 있다면, 담당자와 연락처, 어떤 관계(요청·협의·보고)인지 알려 주시겠어요?",
    ),
    SlotSpec(
        key="regular",
        title="정기 업무",
        description="일·주·월·분기·연 단위로 정기성이 있는 세부 업무. 시기와 절차, 그리고 업무 히스토리(주요 결정·협의사항)와 후임자에게 남기는 말을 담는다.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 차액 정산 수행"),
            FieldSpec("intro", "소개"),
            FieldSpec("timing", "시기", "주기와 기한. 예: 매월 5영업일 이내"),
            FieldSpec("procedure", "수행 절차", "핵심 단계를 한 줄로"),
            FieldSpec("key_points", "중요하게 봐야 하는 것", "놓치면 사고가 나는 점, 판단 기준"),
            FieldSpec("decisions", "주요 결정사항", "무엇을 왜 그렇게 결정했는지(근거 포함)"),
            FieldSpec("consultations", "주요 협의사항", "유관 부서·기관과 합의한 내용"),
            FieldSpec("expectations", "후임자에게 바라는 것", "앞으로 발전시켜 갔으면 하는 방향"),
            FieldSpec("tips", "추천 팁", "읽어 볼 자료·서적·법령, 관행과 요령"),
        ),
        required=("name", "timing", "procedure", "key_points", "decisions"),
        min_items=1,
        leave=True,
        queries=("매일 매주 매월 정기 반복 업무", "주기적 점검 보고 일정 기한", "연간 일정 분기 갱신 신청", "업무 절차 처리 방법"),
        keywords=("매일", "매주", "매월", "분기", "매년", "정기", "주기", "월간", "주간", "연간", "영업일", "절차"),
        default_question="정기적으로 반복하는 세부 업무(매일·매월·매년 등)가 있다면, 업무명과 시기, 수행 절차를 알려 주시겠어요?",
        layout="card",
    ),
    SlotSpec(
        key="irregular",
        title="비정기 업무",
        description="프로젝트처럼 비정기로 수행하는 세부 업무. 배경·목표·진행 경과·미결과제와 이해관계자와의 협의 사항을 담는다.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 은행 대리업 구축 프로젝트"),
            FieldSpec("background", "소개 및 배경", "왜 시작됐는지"),
            FieldSpec("goal", "목표"),
            FieldSpec("progress", "진행 경과", "지금까지의 경과와 현재 단계"),
            FieldSpec("schedule", "진행 일정", "추후 일정, 부서별 협조사항"),
            FieldSpec("consultations", "이해관계자와의 협의 사항"),
            FieldSpec("open_issues", "미결과제", "남은 일, 결정이 필요한 것"),
            FieldSpec("key_points", "중요하게 봐야 하는 것"),
            FieldSpec("decisions", "주요 결정사항"),
            FieldSpec("expectations", "후임자에게 바라는 점"),
        ),
        required=("name", "background", "goal", "progress", "open_issues"),
        min_items=1,
        leave=True,
        queries=("진행 중인 과제 프로젝트 현황", "추진 배경 목표 일정", "미결 과제 다음 할 일 결정사항", "이해관계자 협의 사항"),
        keywords=("과제", "프로젝트", "추진", "진행", "예정", "사업", "입찰", "검토 중", "배경", "목표", "미결"),
        default_question="진행 중이거나 곧 시작될 프로젝트성 업무가 있다면, 시작 배경과 목표, 현재 진행 상황을 알려 주시겠어요?",
        layout="card",
    ),
    SlotSpec(
        key="systems",
        title="시스템 접근 계정",
        description="업무에 쓰는 시스템·도구, 용도, 필요한 권한 수준과 권한 신청·이관 방법. ID/PW 는 보안상 AI 에 입력하지 않고 문서에서 수기로 적는다.",
        fields=(
            FieldSpec("name", "시스템"),
            FieldSpec("purpose", "용도"),
            FieldSpec("access", "권한 수준", "관리자/편집자/조회 등"),
            FieldSpec("how_to_get", "신청·이관 방법"),
        ),
        required=("name", "purpose", "how_to_get"),
        min_items=1,
        leave=True,
        queries=("사용 시스템 접근 권한 계정", "권한 신청 방법 이관 승인", "관리자 계정 콘솔 접속"),
        keywords=("시스템", "권한", "계정", "콘솔", "관리자", "접속", "CMS", "로그인"),
        default_question="업무에 사용하는 시스템과, 인수자가 권한을 받으려면 어떻게 신청해야 하는지 알려 주시겠어요? (ID/PW 는 적지 마세요)",
        handwritten=("ID / PW (수기 기재)",),
    ),
    SlotSpec(
        key="dept_notes",
        title="부서별 특이사항",
        description="부서 업무의 특성상 반드시 챙겨야 하는 사항(외주 관리, 가맹점 관리, 라이선스 등)과 근거가 되는 인수인계 지침.",
        fields=(
            FieldSpec("topic", "특이사항", "예: 가맹점 관리, 외주 관리, 라이선스"),
            FieldSpec("detail", "관리 내용", "무엇을 어떻게 챙겨야 하는지"),
            FieldSpec("basis", "근거 지침", "세부시행세칙·인수인계 지침 등"),
        ),
        required=("topic", "detail"),
        min_items=1,
        queries=("부서별 특이사항 외주 관리 가맹점 관리", "라이선스 라이센스 관리 계약", "인수인계 지침 세부시행세칙"),
        keywords=("외주", "가맹점", "라이선스", "라이센스", "위탁", "특이", "지침", "세칙"),
        default_question="소속 부서만의 특이 관리 사항(외주 관리, 가맹점 관리, 라이선스 등)이 있나요? 없으면 '해당 없음'으로 건너뛰어 주세요.",
    ),
)

SLOT_BY_KEY: dict[str, SlotSpec] = {s.key: s for s in SLOTS}
SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
ALL_FIELD_KEYS: tuple[str, ...] = tuple(dict.fromkeys(f.key for s in SLOTS for f in s.fields))


def mode_of(profile: dict[str, Any] | None) -> str:
    """인계자 정보의 `mode` 를 정규화한다(없거나 모르는 값이면 인사발령 전체 양식)."""
    mode = str((profile or {}).get("mode") or "").strip().lower()
    return MODE_LEAVE if mode == MODE_LEAVE else MODE_REASSIGNMENT


def slots_for(profile: dict[str, Any] | None) -> tuple[SlotSpec, ...]:
    """이번 인수인계서에 쓰는 슬롯(장기휴가 버전은 Light 구성)."""
    if mode_of(profile) == MODE_LEAVE:
        return tuple(s for s in SLOTS if s.leave)
    return SLOTS


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
