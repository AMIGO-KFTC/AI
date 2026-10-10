"""인수인계서 양식(슬롯) 정의.

인수인계서의 각 장(章)을 '슬롯'으로 보고, 슬롯마다 채워야 할 필드·필수 필드·검색어·질문 예시를 정의한다.
STAGE 1(분석)은 슬롯별로 근거를 찾아 항목을 채우고, 필수 필드가 비어 있으면 '공백(gap)'으로 판정해
STAGE 3(Q&A)에서 한 번에 하나씩 질문한다. 사내 양식이 바뀌면 이 파일만 고치면 된다.

양식은 10/4 아이디어 구체화 회의(가회·윤희 버전)의 합집합이다.
- 인수인계 유형(MODES): 인사발령(전체 양식) / 장기휴가(Light: 업무 소개·정기 업무·이해관계자·시스템 접근 계정)
- 업무 단위별로 1건의 인수인계서를 만든다(profile.task_name = 단위 업무명).
- recall: 업무 히스토리(결정 배경·협의·요령)를 떠올리게 하는 회상 질문. 빈 필드마다 질문 후보가 된다.
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
    # 회상 질문: (필드 키, 질문 서식). 서식의 {name} 은 항목명으로 바뀐다. 필수 필드가 아니어도 비어 있으면 낮은 우선순위로 묻는다.
    recall: tuple[tuple[str, str], ...] = ()
    modes: tuple[str, ...] = ("transfer", "leave")

    @property
    def title_field(self) -> str:
        return self.fields[0].key

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    def label(self, key: str) -> str:
        spec = self.field(key)
        return spec.label if spec else key


# 인수인계 유형. 장기휴가는 Light 버전이라 질문 수도 줄인다.
MODES: dict[str, str] = {"transfer": "인사발령", "leave": "장기휴가"}
DEFAULT_MODE = "transfer"
MODE_MAX_QUESTIONS = {"leave": 6}

SLOTS: tuple[SlotSpec, ...] = (
    SlotSpec(
        key="duties",
        title="업무 소개",
        description="단위 업무(예: CD공동망 운영)의 대략적인 소개. 업무지침·관계법령·업무매뉴얼을 명시한다.",
        fields=(
            FieldSpec("name", "단위 업무명", "예: CD공동망 운영"),
            FieldSpec("description", "업무 소개", "3줄 이내로 어떤 업무인지"),
            FieldSpec("guideline", "업무지침", "내규·지침 명칭"),
            FieldSpec("law", "관계법령", "근거 법령·규정"),
            FieldSpec("manual", "업무매뉴얼", "매뉴얼 이름·위치"),
        ),
        required=("name", "description"),
        min_items=1,
        queries=("담당 업무 주요 업무 업무 소개 개요", "업무지침 관계법령 업무매뉴얼 근거 규정", "업무 분장 역할 책임 범위"),
        keywords=("업무", "담당", "관리", "운영", "역할", "수행", "지침", "법령", "매뉴얼"),
        default_question="인계하실 단위 업무가 어떤 업무인지 3줄 이내로 소개해 주시겠어요? 관련 업무지침·관계법령·매뉴얼이 있으면 함께 알려 주세요.",
    ),
    SlotSpec(
        key="stakeholders",
        title="이해관계자",
        description="업무상 협업하는 내부 부서와 외부 기관·업체, 관계 유형(요청·협의·보고), 담당자와 연락처.",
        fields=(
            FieldSpec("party", "부서·기관·업체"),
            FieldSpec("side", "구분", "내부 / 외부"),
            FieldSpec("relation", "관계", "요청 / 협의 / 보고"),
            FieldSpec("role", "협업 내용"),
            FieldSpec("person", "담당자", "이름과 직위"),
            FieldSpec("contact", "연락처", "내선, 메일, 휴대폰 등"),
            FieldSpec("note", "관계 메모", "최근 논의 내용, 까다로운 점 등"),
        ),
        required=("party", "person", "role", "contact"),
        min_items=1,
        queries=("협업 부서 담당자 연락처", "유관 부서 협의 요청 보고", "외부 기관 업체 담당자 계약"),
        keywords=("팀", "부서", "업체", "담당자", "연락", "협의", "협업", "내선", "기관", "요청", "보고"),
        default_question="업무상 자주 협업하는 부서나 외부 기관·업체, 그리고 담당자와 연락처를 알려 주시겠어요? (요청·협의·보고 중 어떤 관계인지도 함께요)",
        recall=(("note", "'{name}'과(와) 최근에 나눈 논의나, 인수자가 미리 알아야 할 관계상의 특이점이 있나요?"),),
    ),
    SlotSpec(
        key="recurring",
        title="정기 업무",
        description="일·주·월·분기·연 단위로 반복되는 세부 업무. 시기, 수행 절차, 결정·협의 이력, 요령을 포함한다.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 차액 정산 수행"),
            FieldSpec("intro", "소개"),
            FieldSpec("cycle", "주기", "매일/매주/매월/분기/매년"),
            FieldSpec("timing", "시기·기한", "예: 매월 5영업일 이내"),
            FieldSpec("procedure", "수행 절차", "핵심 단계를 한 줄로"),
            FieldSpec("key_points", "중요하게 봐야 하는 것"),
            FieldSpec("decisions", "주요 결정사항·근거", "과거에 정한 방식과 그 이유"),
            FieldSpec("consultations", "주요 협의사항", "부서·기관과 협의한 내용"),
            FieldSpec("future", "발전시킬 사항", "후임자가 개선해 갈 방향"),
            FieldSpec("tips", "추천 팁", "추천 자료·서적·법령·관행·요령"),
            FieldSpec("wishes", "후임자에게 바라는 점"),
            FieldSpec("related", "관련 부서·시스템", "예: 재무팀, CMS"),
        ),
        required=("name", "cycle", "timing"),
        min_items=1,
        queries=("매일 매주 매월 정기 반복 업무", "주기적 점검 보고 일정 기한", "연간 일정 분기 갱신 신청"),
        keywords=("매일", "매주", "매월", "분기", "매년", "정기", "주기", "월간", "주간", "연간", "영업일"),
        default_question="정기적으로 반복하는 업무(매일·매주·매월·매년 등)가 있다면 주기와 시기를 알려 주시겠어요?",
        recall=(
            ("decisions", "'{name}'을(를) 하면서 방식을 바꾸거나 예외로 처리하기로 결정했던 적이 있나요? 있다면 무엇을, 왜 그렇게 정했는지 알려 주세요."),
            ("key_points", "'{name}'을(를) 처음 맡는 후임자가 놓치기 쉬워서 특히 확인해야 하는 점은 무엇인가요?"),
            ("consultations", "'{name}'과(와) 관련해 다른 부서나 기관과 협의해서 정한 사항이 있나요?"),
            ("tips", "'{name}'에 도움이 된 자료·서적·법령이나 나만의 요령이 있다면 추천해 주시겠어요?"),
            ("wishes", "'{name}'을(를) 이어받을 후임자에게 바라는 점이나, 앞으로 발전시켰으면 하는 방향이 있나요?"),
        ),
    ),
    SlotSpec(
        key="projects",
        title="비정기 업무",
        description="프로젝트처럼 일정 기간 진행되는 비정기 업무의 배경·목표·진행 경과·미결 과제.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 은행 대리업 구축 프로젝트"),
            FieldSpec("background", "소개 및 배경", "왜 시작됐는지"),
            FieldSpec("goal", "목표"),
            FieldSpec("schedule", "진행 일정", "일정·마감, 부서별 협조사항"),
            FieldSpec("progress", "진행 경과", "현재 단계와 지금까지의 경과"),
            FieldSpec("consultations", "관련 부서·기관과의 협의 내용"),
            FieldSpec("unresolved", "미결 과제", "남은 일, 다음 할 일"),
            FieldSpec("key_points", "중요하게 봐야 하는 것"),
            FieldSpec("decisions", "주요 결정사항"),
            FieldSpec("wishes", "후임자에게 바라는 점"),
        ),
        required=("name", "background", "progress", "unresolved"),
        min_items=1,
        queries=("진행 중인 과제 프로젝트 현황 배경", "추진 일정 마감 예정 목표", "액션 아이템 다음 할 일 미결 결정사항"),
        keywords=("과제", "프로젝트", "추진", "진행", "예정", "사업", "입찰", "검토 중", "구축"),
        default_question="진행 중이거나 곧 시작할 프로젝트성 업무가 있다면 시작 배경과 현재 진행 상황을 알려 주시겠어요?",
        recall=(
            ("decisions", "'{name}'을(를) 진행하면서 중요하게 결정한 사항과 그 이유가 있나요?"),
            ("consultations", "'{name}'과(와) 관련해 내·외부 이해관계자와 협의한 내용 중 인수자가 알아야 할 것이 있나요?"),
            ("key_points", "'{name}'에서 인수자가 가장 신경 써야 할 점은 무엇인가요?"),
            ("wishes", "'{name}'을(를) 이어받을 후임자에게 바라는 점이 있나요?"),
        ),
        modes=("transfer",),
    ),
    SlotSpec(
        key="accounts",
        title="시스템 접근 계정",
        description="업무에 쓰는 시스템과 필요한 권한 수준, 권한 신청·이관 방법. 아이디·비밀번호는 기록하지 않고 출력본에 수기로 적는다.",
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
        default_question="업무에 사용하는 시스템과, 인수자가 권한을 받으려면 어떻게 신청해야 하는지 알려 주시겠어요? (아이디·비밀번호는 말씀하지 마세요)",
    ),
    SlotSpec(
        key="dept",
        title="부서별 특이사항",
        description="소속 부서의 인수인계 지침·세부시행세칙에 따라 추가로 인계해야 하는 항목(외주 관리, 가맹점 관리, 라이센스 등).",
        fields=(
            FieldSpec("topic", "항목", "예: 외주 관리, 가맹점 관리, 라이센스"),
            FieldSpec("content", "인계 내용"),
            FieldSpec("basis", "근거 지침", "인수인계 지침·세부시행세칙의 해당 조항"),
        ),
        required=("topic", "content"),
        min_items=1,
        queries=("부서별 특이사항 인수인계 지침 세부시행세칙", "외주 관리 가맹점 관리 라이센스 계약", "부서 업무 특성 추가 인계 항목"),
        keywords=("외주", "가맹점", "라이센스", "라이선스", "지침", "세칙", "특이"),
        default_question="소속 부서의 인수인계 지침에 따라 따로 인계해야 하는 항목(예: 외주 관리, 가맹점 관리, 라이센스)이 있나요?",
        modes=("transfer",),
    ),
)

SLOT_BY_KEY: dict[str, SlotSpec] = {s.key: s for s in SLOTS}
SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in SLOTS)
ALL_FIELD_KEYS: tuple[str, ...] = tuple(dict.fromkeys(f.key for s in SLOTS for f in s.fields))


def normalize_mode(mode: str | None) -> str:
    return mode if mode in MODES else DEFAULT_MODE


def slots_for_mode(mode: str | None) -> tuple[SlotSpec, ...]:
    mode = normalize_mode(mode)
    return tuple(s for s in SLOTS if mode in s.modes)


def slot_keys_for_mode(mode: str | None) -> tuple[str, ...]:
    return tuple(s.key for s in slots_for_mode(mode))


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
