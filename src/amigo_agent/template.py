"""인수인계서 양식(슬롯) 정의.

인수인계서의 각 장(章)을 '슬롯'으로 보고, 슬롯마다 채워야 할 필드·필수 필드·검색어·질문 예시를 정의한다.
STAGE 1(분석)은 슬롯별로 근거를 찾아 항목을 채우고, 필수 필드가 비어 있으면 '공백(gap)'으로 판정해
STAGE 3(Q&A)에서 한 번에 하나씩 질문한다. 사내 양식이 바뀌면 이 파일만 고치면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass


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
        key="duties",
        title="담당 업무",
        description="인계자가 맡아 온 주요 업무의 범위와 내용. 업무별 비중·중요도와 관련 산출물을 포함한다.",
        fields=(
            FieldSpec("name", "업무명", "예: 홈페이지 콘텐츠 관리"),
            FieldSpec("description", "주요 내용", "무엇을 어떻게 하는지 한 줄"),
            FieldSpec("weight", "비중/중요도", "예: 40%, 높음"),
            FieldSpec("outputs", "산출물·관련 문서", "예: 월간 실적 보고서"),
        ),
        required=("name", "description"),
        min_items=2,
        queries=("담당 업무 주요 업무 업무 내용", "업무 분장 역할 책임 범위", "업무 개요 담당자 수행"),
        keywords=("업무", "담당", "관리", "운영", "역할", "수행"),
        default_question="맡고 계신 주요 업무를 업무명과 함께 간단히 설명해 주시겠어요?",
    ),
    SlotSpec(
        key="recurring",
        title="반복 수행 업무",
        description="일·주·월·분기·연 단위로 반복되는 업무와 수행 시기, 처리 절차.",
        fields=(
            FieldSpec("name", "업무", "예: 유지보수 월간 실적 검수"),
            FieldSpec("cycle", "주기", "매일/매주/매월/분기/매년"),
            FieldSpec("timing", "시기·기한", "예: 매월 5영업일 이내"),
            FieldSpec("procedure", "처리 절차", "핵심 단계를 한 줄로"),
            FieldSpec("related", "관련 부서·시스템", "예: 재무팀, CMS"),
        ),
        required=("name", "cycle", "timing"),
        min_items=1,
        queries=("매일 매주 매월 정기 반복 업무", "주기적 점검 보고 일정 기한", "연간 일정 분기 갱신 신청"),
        keywords=("매일", "매주", "매월", "분기", "매년", "정기", "주기", "월간", "주간", "연간", "영업일"),
        default_question="정기적으로 반복하는 업무(매주·매월·매년 등)가 있다면 주기와 시기를 알려 주시겠어요?",
    ),
    SlotSpec(
        key="projects",
        title="진행 중인 과제",
        description="인계 시점에 진행 중인 과제·프로젝트의 현황, 일정, 다음에 할 일.",
        fields=(
            FieldSpec("name", "과제명"),
            FieldSpec("status", "현황", "진척도, 현재 단계"),
            FieldSpec("schedule", "일정·마감"),
            FieldSpec("next_steps", "다음 할 일"),
            FieldSpec("stakeholders", "관련자·부서"),
        ),
        required=("name", "status", "next_steps"),
        min_items=1,
        queries=("진행 중인 과제 프로젝트 현황", "추진 일정 마감 예정", "액션 아이템 다음 할 일 결정사항"),
        keywords=("과제", "프로젝트", "추진", "진행", "예정", "사업", "입찰", "검토 중"),
        default_question="현재 진행 중인 과제나 프로젝트가 있다면 현황과 다음에 해야 할 일을 알려 주시겠어요?",
    ),
    SlotSpec(
        key="contacts",
        title="협업 관계",
        description="업무상 자주 협업하는 내부 부서·외부 기관·업체와 담당자, 협업 내용, 연락 방법.",
        fields=(
            FieldSpec("party", "부서·기관·업체"),
            FieldSpec("person", "담당자", "이름과 직위"),
            FieldSpec("role", "협업 내용"),
            FieldSpec("contact", "연락처", "내선, 메일, 휴대폰 등"),
        ),
        required=("party", "person", "role", "contact"),
        min_items=1,
        queries=("협업 부서 담당자 연락처", "유관 부서 협의 요청", "외부 업체 담당자 계약"),
        keywords=("팀", "부서", "업체", "담당자", "연락", "협의", "협업", "내선", "기관"),
        default_question="업무상 자주 협업하는 부서나 외부 업체, 그리고 담당자 연락처를 알려 주시겠어요?",
    ),
    SlotSpec(
        key="systems",
        title="사용 시스템 및 접근 권한",
        description="업무에 쓰는 시스템·도구, 용도, 필요한 권한 수준과 권한 신청·이관 방법.",
        fields=(
            FieldSpec("name", "시스템"),
            FieldSpec("purpose", "용도"),
            FieldSpec("access", "권한 수준", "관리자/편집자/조회 등"),
            FieldSpec("how_to_get", "신청·이관 방법"),
        ),
        required=("name", "purpose", "how_to_get"),
        min_items=1,
        queries=("사용 시스템 접근 권한 계정", "권한 신청 방법 이관 승인", "관리자 계정 콘솔 접속"),
        keywords=("시스템", "권한", "계정", "콘솔", "관리자", "접속", "CMS", "로그인"),
        default_question="업무에 사용하는 시스템과, 인수자가 권한을 받으려면 어떻게 신청해야 하는지 알려 주시겠어요?",
    ),
    SlotSpec(
        key="issues",
        title="주요 이슈 및 유의사항",
        description="미해결 이슈, 리스크, 마감이 임박한 일, 인수자가 특히 주의해야 할 점.",
        fields=(
            FieldSpec("title", "이슈"),
            FieldSpec("status", "현황"),
            FieldSpec("action", "대응 방안·유의사항"),
            FieldSpec("due", "기한"),
        ),
        required=("title", "action"),
        min_items=1,
        queries=("이슈 문제 리스크 유의사항 주의", "장애 지연 미확정 만료", "결정 필요 사항 기한 승인"),
        keywords=("이슈", "문제", "장애", "리스크", "유의", "주의", "미확정", "지연", "만료", "반드시"),
        default_question="인수자가 특히 주의해야 할 이슈나 리스크, 기한이 임박한 일이 있을까요?",
    ),
)

SLOT_BY_KEY: dict[str, SlotSpec] = {s.key: s for s in SLOTS}
SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
ALL_FIELD_KEYS: tuple[str, ...] = tuple(dict.fromkeys(f.key for s in SLOTS for f in s.fields))

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
