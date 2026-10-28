"""인수인계서 양식(슬롯) 정의.

노션 '인수인계서 형식' 페이지의 최종 템플릿과 '수정사항' 페이지(10.25)를 따른다. 인수인계서는 두 가지 버전이며, 버전은 인계자 정보의 `mode` 로 고른다.

[인사발령]  A. 업무 단위 요약 + 일정 명세표(월별 막대) → B. 정기업무(협업관계는 각 업무 안) →
            C. 비정기 업무(프로젝트성, 추진 경과·협업관계는 각 프로젝트 안) → D. 기타 자료(시스템 접근 권한 · 부서별 추가 자료)
[장기휴가]  A. 세부 업무 → B. 주요 연락망 → C. 기타 자료(시스템 접근 권한)

항목 이름은 페이지의 항목을, `hint` 는 그 항목의 '핵심 질문'을 그대로 옮긴 것이다(질문 설계와 AI 지침에 쓰인다).
항목마다 묻는 방식(FieldSpec.ask)이 정해져 있다.
- main(필수 질문): 비어 있으면 묻는다. 노션 양식의 질문 예시가 있으면 그 문장으로 묻는다.
- aux(보조질문): 비어 있으면 묻되, 화면에 '보조질문' 태그와 '꼭 답하지 않아도 된다'는 안내를 붙인다.
- auto(미질의): 묻지 않는다. 자료와 답변으로 채우고, 문서를 만들기 직전에 비어 있으면 수집된 내용으로 요약해 넣는다.
- hidden: 문서에 싣지 않는 내부 값(협업 대상의 내부/외부, 추진 경과의 상태, 연결 필드 등). 묻지 않는다.
`gate` 가 켜진 항목은 코드가 묻는 질문이면 먼저 예/아니요로 묻고, '예'일 때 주관식으로 묻는다(아니요면 '해당 없음').
`picker="period"` 인 항목(수행 시기)은 주기(매일·매주·매월·매년)를 고르거나, 정기적이지 않으면 달력에서 기간을 고른다.

인수인계서의 각 장(章)을 '슬롯'으로 보고, 슬롯마다 채워야 할 필드·검색어·질문 예시를 정의한다.
STAGE 1(분석)은 슬롯별로 근거를 찾아 항목을 채우고, 묻는 항목이 비어 있으면 '공백(gap)'으로 판정해
STAGE 3(Q&A)에서 한 번에 하나씩 질문한다. 사내 양식이 바뀌면 이 파일과 field_guides.py 만 고치면 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODE_REASSIGNMENT = "reassignment"  # 인사발령
MODE_LEAVE = "leave"  # 장기휴가
MODE_LABELS = {MODE_REASSIGNMENT: "인사발령", MODE_LEAVE: "장기휴가"}

ASK_MAIN = "main"  # 필수 질문
ASK_AUX = "aux"  # 보조질문
ASK_AUTO = "auto"  # 미질의(묻지 않고 자료·답변으로 채운다)
ASK_HIDDEN = "hidden"  # 문서에 싣지 않는 내부 값

PERIOD_CHOICES = ("매일", "매주", "매월", "매년")  # 수행 시기의 주기 선택지(정기적이지 않으면 달력에서 기간을 고른다)


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    hint: str = ""
    ask: str = ASK_MAIN
    gate: bool = False  # 코드가 묻는 질문이면 먼저 예/아니요로 묻는다(예 → 주관식, 아니요 → '해당 없음')
    specific: bool = False  # 보조·예/아니요 항목이라도 자료에서 구체적인 사례를 찾았으면 AI 가 그 사례를 들어 바로 묻는다
    picker: str = ""  # "period": 주기 선택 또는 달력 기간 선택
    answers_only: bool = False  # 업로드 자료로는 채우지 않고 질의응답으로만 채운다
    placeholder: str = ""  # 묻지 않는 칸이 비었을 때 문서에 흐리게 보여 줄 안내(인계자가 직접 적거나 행을 지운다)


@dataclass(frozen=True)
class SlotSpec:
    key: str
    title: str
    description: str
    fields: tuple[FieldSpec, ...]
    min_items: int
    queries: tuple[str, ...]
    keywords: tuple[str, ...]
    default_question: str
    modes: tuple[str, ...] = (MODE_REASSIGNMENT,)  # 이 장을 쓰는 인수인계서 버전
    layout: str = "table"  # 문서에서 항목을 표(table), 항목별 카드(card), 월별 막대 일정표(gantt)로 보일지
    handwritten: tuple[str, ...] = ()  # AI 가 채우지 않고 문서에 빈칸으로 남기는 열(예: ID/PW)
    evidence: bool = True  # 문서에 근거 번호([1] …)를 달지
    # 이 장을 따로 싣지 않고 다른 장의 카드 안에 넣는다. embed_into=받는 장, embed_link=받는 장의 항목 이름과 맞출 필드,
    # embed_after=받는 장의 이 필드 행 뒤에 넣는다, embed_label=카드 안의 행 이름
    embed_into: str = ""
    embed_link: str = ""
    embed_after: str = ""
    embed_label: str = ""
    title_key: str = ""  # 항목 이름으로 쓸 필드(비우면 첫 필드). 표의 열 순서와 항목 이름 필드가 다를 때 쓴다
    # 조건부 묻는 방식: (필드, 조건 필드, 조건 값들, 묻는 방식). 조건 필드 값에 조건 값 중 하나가 들어 있으면 그 방식으로, 아니면 필드의 기본 방식으로 묻는다.
    ask_when: tuple[tuple[str, str, tuple[str, ...], str], ...] = ()
    # 장에 항목이 하나도 없을 때 default_question 을 물을지("" 이면 묻지 않음, main/aux). 인계자가 세부업무를 입력했으면 묻지 않는다(nodes).
    empty_ask: str = ""
    seed: str = ""  # 인계자 정보에서 항목을 미리 만든다: units(단위업무) / regular / irregular(세부업무) / leave(장기휴가 세부업무)
    group: str = ""  # 여러 장을 한 장 제목 아래 소제목으로 묶는다(기타 자료)
    split_field: str = ""  # 표를 이 필드 값(외부/내부)으로 나눠 싣는다
    split_drop: tuple[tuple[str, tuple[str, ...]], ...] = ()  # 나눈 표마다 뺄 열(내부 표에는 연락처 열이 없다)
    muted_note: str = ""  # 화면에서만 이 장 위에 회색으로 보이는 작성 안내(내려받기·전송·챗봇 자료에서는 빠진다)
    # 받는 장의 항목마다 한 번 묻는 예/아니요 질문(협업 유의사항). 예면 follow_question 으로 묻는다. {name} = 받는 장의 항목 이름
    gate_question: str = ""
    follow_question: str = ""

    @property
    def leave(self) -> bool:
        """장기휴가 버전에도 들어가는 장인지(화면의 양식 안내에서 쓴다)."""
        return MODE_LEAVE in self.modes

    @property
    def title_field(self) -> str:
        return self.title_key or self.fields[0].key

    @property
    def required(self) -> tuple[str, ...]:
        """필수 질문 항목(조건부 항목은 조건이 맞을 때 필수가 되므로 함께 센다)."""
        conditional = {key for key, _, _, ask in self.ask_when if ask == ASK_MAIN}
        return tuple(f.key for f in self.fields if f.ask == ASK_MAIN or f.key in conditional)

    @property
    def asked(self) -> tuple[str, ...]:
        """묻는 항목(필수 질문 + 보조질문)."""
        conditional = {key for key, _, _, _ in self.ask_when}
        return tuple(f.key for f in self.fields if f.ask in (ASK_MAIN, ASK_AUX) or f.key in conditional)

    @property
    def auto(self) -> tuple[str, ...]:
        """묻지 않고 자료·답변으로 채우는 항목(항목 이름 제외)."""
        return tuple(f.key for f in self.fields if f.ask == ASK_AUTO and f.key != self.title_field)

    @property
    def shown(self) -> tuple[FieldSpec, ...]:
        """문서에 싣는 필드(내부 값·연결 필드 제외)."""
        return tuple(f for f in self.fields if f.ask != ASK_HIDDEN and f.key != self.embed_link)

    def field(self, key: str) -> FieldSpec | None:
        return next((f for f in self.fields if f.key == key), None)

    def label(self, key: str) -> str:
        spec = self.field(key)
        return spec.label if spec else key

    def ask_of(self, key: str, fields: dict[str, Any] | None = None) -> str:
        """그 항목에서 이 필드를 묻는 방식(조건부 방식은 항목 값을 보고 정한다)."""
        spec = self.field(key)
        if spec is None:
            return ASK_HIDDEN
        for name, when, values, ask in self.ask_when:
            if name == key:
                actual = str((fields or {}).get(when, ""))
                return ask if any(v in actual for v in values) else spec.ask
        return spec.ask


BOTH = (MODE_REASSIGNMENT, MODE_LEAVE)
FORM_OFFICE = "office"  # 팀장이 쓰는 인수인계서: template 의 양식 대신 원내 양식(OFFICE_ITEMS)으로 만든다(profile["form"])

# 관리자(팀장) 인사발령 인수인계서(노션 '추가 요구사항 정의' 5) · '인수인계서 형식'의 [관리자 인사발령]): 원내 양식 1~15번 + 해당 팀만 16번.
#   1. 주요 사업계획 및 집행상황표 — 시작할 때 고른 주요 사업계획(profile["plans"])마다 계획기간 막대와 비고(진행상황 요약). 질문함
#   2. 주요 현안사항 — 현안이 있다고 고른 사업계획·직접 추가한 현안(profile["issues"])마다 가. 개요 / 나. 진행상황 / 다. 고려사항. 질문함
#   3. 제계약서 명세표, 5~15번 — 직전 인수인계서·첨부 자료에서만 산출(OFFICE_ITEMS, 질문하지 않음)
#   4. 주요 미결사항의 경과 및 처리대책 — <1> 미결 업무가 있는지 묻고, 있으면 <2> 경과·조치를 묻는다
#   16. 부서·팀(반, 사무소)별 추가자료 — 부·팀 인수인계 목록이 있는 팀만, 목록의 자료를 올리게 한다(render)
OFFICE_FIRST_TITLE = "1. 주요 사업계획 및 집행상황표"
OFFICE_ITEMS: tuple[tuple[str, str], ...] = (  # (목록, 비고). 비고는 AI 가 내용을 쓸 때 참고하는 사항이라 문서에는 싣지 않는다
    ("3. 제계약서 명세표", ""),
    ("5. 중요 인장 보관 명세표", "인장명, 인영, 보관자 직책·성명"),
    ("6. 보관문서목록표", ""),
    ("7. 전산기기 및 관련 통신장비 보유현황", "리스 도입분 포함"),
    ("8. 고정자산 및 비품 명세표", ""),
    ("9. 보안업무 관련사항", ""),
    ("10. 법인신용카드보관명세표", ""),
    ("11. 담당규정목록", "부서간·팀(반, 사무소)간 업무이관시"),
    ("12. 실무지침", ""),
    ("13. 담당업무 매뉴얼", "최신 내용으로 정비여부 확인"),
    ("14. 주기적 업무체크리스트", ""),
    ("15. 기타 주요사항", "다른 목록에 들어가지 않는 사항만. 1번 사업계획·2번 현안·4번 미결사항에 쓰는 내용은 다시 쓰지 않음"),
)
OFFICE_DEPT_TITLE = "16. 부서·팀(반, 사무소)별 추가자료"
OFFICE_PENDING_QUESTION = (
    "현재까지 처리되지 않았거나, 후임자가 이어서 처리해야 할 주요 업무가 있나요? "
    "처리가 지연되거나 검토·협의·승인 등이 완료되지 않은 업무를 작성해 주세요."
)
OFFICE_PENDING_FOLLOW = "현재까지 어떤 절차나 조치가 진행되었나요? / 해당 사항을 해결하기 위해 앞으로 어떤 조치가 필요한가요?"
OTHER_MATERIALS = "기타 자료"
PARTNER_SCOPES = ("외부", "내부")  # 협업 대상 표를 나누는 순서. 우리 기관 소속이면 내부, 그 밖(외부 기관·업체)은 외부

# 슬롯 순서는 문서 순서이자 질문 순서다. 버전별로 걸러 쓰므로(slots_for) 두 버전의 장 순서가 각각 템플릿과 같다.
SLOTS: tuple[SlotSpec, ...] = (
    # ------------------------------------------------------------------ [인사발령] A
    SlotSpec(
        key="overview",
        title="업무 단위별 일정 명세표",
        description="인계하는 단위 업무(업무분장상 단위업무, 예: 공동망 운영, 40년사 발간 추진)와 업무별 계획기간(월별 일정). "
        "단위업무는 인계자가 직제규정 시행세칙에서 고른 것이며, 이 장은 질문하지 않습니다. 계획기간은 사업계획서·추진계획 등 자료에서 읽고, "
        "진행상황 요약은 문서를 만들 때 지금까지 수집된 내용으로 자동 작성합니다.",
        fields=(
            FieldSpec("name", "단위업무명", "예: 공동망 운영", ask=ASK_AUTO),
            FieldSpec("period", "계획기간", "예: 2026.03~2026.12, 매월 상시", ask=ASK_AUTO),
            FieldSpec("note", "비고", "진행상황 요약: 단위 업무가 지금 어디까지 진행되었는가?(핵심 성과·현재·다음 단계, 1~2문장 80자 안팎)", ask=ASK_AUTO),
        ),
        min_items=1,
        queries=("단위업무 업무 개요 계획 일정", "연간 계획 추진 일정 기간", "담당 업무 목록 업무 분장", "추진 현황 진행 상황"),
        keywords=("업무", "담당", "계획", "기간", "일정", "추진", "운영", "현황", "진행"),
        default_question="인계하실 단위 업무를 업무명과 계획기간(언제부터 언제까지, 또는 상시)과 함께 알려 주시겠어요?",
        layout="gantt",
        evidence=False,
        seed="units",
        muted_note="실제 업무 추진 현황 및 예상일정을 막대 그래프로 나타내주세요",
    ),
    # ------------------------------------------------------------------ [인사발령] B
    SlotSpec(
        key="regular",
        title="정기업무",
        description="일·주·월·분기·연 단위로 정기성이 있는 세부 업무. 인계자가 단위업무마다 입력한 정기 세부업무가 항목이다. "
        "개요·시기·절차·중점 관리사항·의사결정 근거·협업관계·향후 과제·기타(참고자료 등)를 담는다(주요 협업관계는 별도 장 partners 의 표).",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 월간 경영실적 보고 업무"),
            FieldSpec("unit", "단위업무명", "이 세부업무가 속한 단위업무", ask=ASK_HIDDEN),
            FieldSpec("overview", "업무 개요", "무엇을, 왜 하는가?", ask=ASK_AUTO),
            FieldSpec("timing", "수행 시기", "언제 하는가?", picker="period"),
            FieldSpec("procedure", "수행 절차", "어떻게 하는가?"),
            FieldSpec("key_points", "중점 관리사항", "무엇을 중요하게 관리하고 주의해야 하는가?", gate=True, specific=True),
            FieldSpec("decisions", "주요 의사결정 및 판단 근거", "무엇을, 왜 그렇게 결정했는가?", gate=True, specific=True),
            FieldSpec("future", "향후 과제 및 발전 방향", "무엇을 이어가고 개선해야 하는가?", ask=ASK_AUX, gate=True),
            FieldSpec("references", "기타(참고자료 등)", "어떤 자료와 규정을 참고해야 하는가?", ask=ASK_AUTO,
                      placeholder="인수자가 참고할 좋은 노하우, 팁, 참고자료가 있다면 작성해 주세요."),
        ),
        min_items=1,
        queries=("매일 매주 매월 정기 반복 업무", "주기적 점검 보고 일정 기한", "연간 일정 분기 갱신 신청", "업무 절차 처리 방법"),
        keywords=("매일", "매주", "매월", "분기", "매년", "정기", "주기", "월간", "주간", "연간", "영업일", "절차"),
        default_question="정기적으로 반복하는 세부 업무(매일·매월·매년 등)가 있다면, 업무명과 수행 시기, 수행 절차를 알려 주시겠어요?",
        layout="card",
        empty_ask=ASK_MAIN,
        seed="regular",
    ),
    SlotSpec(
        key="partners",
        title="정기업무 주요 협업관계",
        description="정기업무를 하면서 함께 일하는 부서·사람. 문서에서는 각 정기업무의 '주요 협업관계' 칸에 외부(소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항))와 "
        "내부(소속 | 이름 | 주요역할 | 비고(유의사항)) 표로 나눠 들어간다. 메일·메신저·회의록·연락처 자료에서 찾아 채우고, 유의사항만 인계자에게 묻는다.",
        fields=(
            FieldSpec("org", "소속", "팀·부서·기관·업체. 예: 재무팀", ask=ASK_AUTO),
            FieldSpec("name", "이름", "사람 이름(직급 포함 가능) 또는 팀·기관명만. 예: 박지훈 차장, 재무팀, (주)한빛웹솔루션. 업무명·시스템명은 쓰지 않는다", ask=ASK_AUTO),
            FieldSpec("role", "주요역할", "이 업무에서 무엇을 함께 하는가? 예: 메신저로 실적자료 제출 요청·보고", ask=ASK_AUTO),
            FieldSpec("phone", "연락처", "전화·내선·메일(외부 협업 대상만)", ask=ASK_AUTO),
            FieldSpec("note", "비고(유의사항)", "협업할 때 무엇을 주의해야 하는가?", ask=ASK_AUTO),
            FieldSpec("scope", "구분", "내부(우리 기관 소속 부서·사람) 또는 외부(다른 기관·업체) 중 하나", ask=ASK_HIDDEN),
            FieldSpec("task", "세부 업무명", "어느 정기업무에서의 협업인지", ask=ASK_HIDDEN),
        ),
        min_items=1,
        queries=("정기 업무 협업 부서 담당자 연락처", "유관 부서 협조 요청 담당자", "업체 담당자 연락처 역할", "협업 시 유의사항"),
        keywords=("팀", "부서", "업체", "담당자", "협업", "협조", "연락처", "내선", "역할", "유의"),
        default_question="정기업무를 하면서 함께 일하는 부서나 담당자가 있다면, 어느 업무에서 누구(소속·이름)와 무엇을 하는지 알려 주시겠어요?",
        evidence=False,
        embed_into="regular",
        embed_link="task",
        embed_after="decisions",
        embed_label="주요 협업관계",
        title_key="name",
        split_field="scope",
        split_drop=(("내부", ("phone",)),),
    ),
    # ------------------------------------------------------------------ [인사발령] C
    SlotSpec(
        key="irregular",
        title="비정기 업무(프로젝트성)",
        description="프로젝트처럼 비정기로 수행하는 세부 업무. 인계자가 단위업무마다 입력한 비정기 세부업무가 항목이다. "
        "프로젝트 개요, 추진 경과(별도 장 milestones 의 표), 주요 협업관계(별도 장 stakeholders 의 표), 주요 의사결정 및 판단 근거, 기타(중점 관리사항)를 담는다.",
        fields=(
            FieldSpec("name", "프로젝트명", "예: 은행 대리업 구축 프로젝트"),
            FieldSpec("unit", "단위업무명", "이 프로젝트가 속한 단위업무", ask=ASK_HIDDEN),
            FieldSpec("overview", "프로젝트 개요", "무엇을, 왜 추진하는가?", ask=ASK_AUTO),
            FieldSpec("decisions", "주요 의사결정 및 판단 근거", "무엇을, 왜 그렇게 결정했는가?", gate=True, specific=True),
            FieldSpec("key_points", "기타(중점 관리사항)", "무엇을 중요하게 관리하고 주의해야 하는가?", ask=ASK_AUX, specific=True),
        ),
        min_items=1,
        queries=("진행 중인 과제 프로젝트 현황", "추진 배경 목표 기대성과", "추진 경과 향후 과제 결정사항", "사업추진 계획"),
        keywords=("과제", "프로젝트", "추진", "진행", "예정", "사업", "입찰", "검토 중", "배경", "목표", "경과"),
        default_question="진행 중이거나 곧 시작될 프로젝트성 업무가 있다면, 추진 배경과 목표, 현재 진행 상황을 알려 주시겠어요?",
        layout="card",
        empty_ask=ASK_MAIN,
        seed="irregular",
    ),
    SlotSpec(
        key="milestones",
        title="비정기 업무 추진 경과",
        description="프로젝트의 주요 추진 단계를 시기순으로 한 줄씩(시기 | 주요 추진 내용 | 비고). 문서에서는 각 프로젝트의 '추진 경과' 칸에 표로 들어간다. "
        "단계마다 상태(완료/진행/계획)를 내부 값으로 둔다: 인계 시점 기준 끝난 일은 완료, 지금 하고 있는 일은 진행, 앞으로 할 일은 계획.",
        fields=(
            FieldSpec("when", "시기", "예: 2026.03, 2026.06~09", ask=ASK_AUTO),
            FieldSpec("content", "주요 추진 내용", "그 시기에 무엇을 했는가(할 것인가)? 예: 사용자 요구사항 조사 완료", ask=ASK_AUTO),
            FieldSpec("note", "비고", "산출물·특이사항", ask=ASK_AUTO),
            FieldSpec("status", "상태", "완료 / 진행 / 계획 중 하나", ask=ASK_HIDDEN),
            FieldSpec("project", "프로젝트명", "어느 프로젝트의 경과인지", ask=ASK_HIDDEN),
        ),
        min_items=1,
        queries=("추진 경과 일정 단계 완료", "추진 계획 일정표 마일스톤", "현재 진행 단계 시범운영"),
        keywords=("경과", "추진", "완료", "진행", "예정", "계획", "단계", "착수", "오픈"),
        default_question="지금까지 어떤 단계로 추진되었으며, 현재 진행 상황과 완료된 주요 성과는 무엇인가요?",
        evidence=False,
        embed_into="irregular",
        embed_link="project",
        embed_after="overview",
        embed_label="추진 경과",
        title_key="content",
    ),
    SlotSpec(
        key="stakeholders",
        title="비정기 업무 주요 협업관계",
        description="프로젝트를 하면서 함께 일하는 부서·기관·사람. 문서에서는 각 프로젝트의 '주요 협업관계' 칸에 정기업무와 같은 형식으로 "
        "외부(소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항))와 내부(소속 | 이름 | 주요역할 | 비고(유의사항)) 표로 나눠 들어간다. 유의사항만 인계자에게 묻는다.",
        fields=(
            FieldSpec("org", "소속", "팀·부서·기관·업체. 예: 정보화부서", ask=ASK_AUTO),
            FieldSpec("name", "이름", "사람 이름(직급 포함 가능) 또는 팀·기관명만. 업무명·시스템명은 쓰지 않는다", ask=ASK_AUTO),
            FieldSpec("role", "주요역할", "이 프로젝트에서 무엇을 함께 했고, 앞으로 어떤 협의가 필요한가?", ask=ASK_AUTO),
            FieldSpec("phone", "연락처", "전화·내선·메일(외부 협업 대상만)", ask=ASK_AUTO),
            FieldSpec("note", "비고(유의사항)", "협업할 때 무엇을 주의해야 하는가?", ask=ASK_AUTO),
            FieldSpec("scope", "구분", "내부(우리 기관 소속 부서·사람) 또는 외부(다른 기관·업체) 중 하나", ask=ASK_HIDDEN),
            FieldSpec("project", "프로젝트명", "어느 프로젝트에서의 협업인지", ask=ASK_HIDDEN),
        ),
        min_items=1,
        queries=("협업 부서 담당자 협의", "유관 부서 협의 요청 보고", "외부 기관 업체 담당자 협의", "일정 및 부서별 협조사항"),
        keywords=("팀", "부서", "업체", "담당자", "협의", "협업", "기관", "요청", "보고", "협조"),
        default_question="프로젝트를 하면서 협의한 팀이나 담당자가 있다면, 어느 프로젝트에서 누구와 무엇을 협의했는지 알려 주시겠어요?",
        evidence=False,
        embed_into="irregular",
        embed_link="project",
        embed_after="overview",
        embed_label="주요 협업관계",
        title_key="name",
        split_field="scope",
        split_drop=(("내부", ("phone",)),),
        gate_question="'{name}' 프로젝트를 하면서 협업할 때 유의해야 할 사항이 있는 사람(부서)이 있나요?",
        follow_question="'{name}' 프로젝트에서 누구(소속·이름)와 협업할 때 어떤 점을 유의해야 하나요?",
    ),
    # ------------------------------------------------------------------ [장기휴가] A
    SlotSpec(
        key="leave_tasks",
        title="세부 업무",
        description="부재 기간 중 대신 처리할 세부 업무. 인계자가 입력한 세부업무가 항목이다(장기휴가는 정기/비정기로 나누지 않는다). "
        "일정·처리사항, 현재 진행 상태, 처리방법, 주의사항 및 후속조치, 참고자료를 담는다.",
        fields=(
            FieldSpec("name", "세부 업무명", "예: 월간 경영실적 보고"),
            FieldSpec("task_type", "업무 유형", "예전 세션의 정기/비정기 구분(장기휴가는 나누지 않으므로 문서에 싣지 않음)", ask=ASK_HIDDEN),
            FieldSpec("schedule", "주요 일정 및 처리사항", "부재 기간 중 언제, 무엇을 처리해야 하는가?"),
            FieldSpec("status", "현재 진행 상태", "현재 진행단계는 어느 정도이며, 다음 진행단계는 어떻게 되는가?", ask=ASK_AUX),
            FieldSpec("method", "업무 처리방법", "어떻게 처리하면 되는가?"),
            FieldSpec("cautions", "주의사항 및 후속조치", "조심할 점, 문제 발생 시 대응, 아직 끝나지 않은 일이 있는가?", ask=ASK_AUX, specific=True),
            FieldSpec("references", "참고자료", "업무에 참고할 수 있는 자료가 어떤 것이 있는가?", ask=ASK_AUX, answers_only=True),
        ),
        min_items=1,
        modes=(MODE_LEAVE,),
        queries=("매일 매주 매월 정기 반복 업무", "처리 방법 절차 일정 마감", "프로젝트 진행 상태 다음 단계", "주의사항 비상 연락 미결 후속 조치"),
        keywords=("일정", "처리", "절차", "방법", "마감", "프로젝트", "진행", "주의", "비상", "미결", "후속", "매뉴얼"),
        default_question="휴가 중에 대신 처리해야 하는 세부 업무가 있다면, 업무명과 처리 일정, 처리 방법을 알려 주시겠어요?",
        layout="card",
        empty_ask=ASK_MAIN,
        seed="leave",
    ),
    # ------------------------------------------------------------------ [장기휴가] B
    SlotSpec(
        key="contacts",
        title="주요 연락망",
        description="부재 중 업무와 관련해 연락할 사람. 인사발령의 주요 협업관계와 같은 형식으로 외부(소속 | 이름 | 주요역할 | 연락처 | 비고(유의사항))와 "
        "내부(소속 | 이름 | 주요역할 | 비고(유의사항)) 표로 나눠 싣는다.",
        fields=(
            FieldSpec("org", "소속", "기관·부서. 예: 금융결제원, 재무팀", ask=ASK_AUTO),
            FieldSpec("name", "이름", "사람 이름(직급 포함 가능) 또는 팀·기관명. 예: 박지훈 차장", ask=ASK_AUTO),
            FieldSpec("role", "주요역할", "어떤 업무로 연락하는가?", ask=ASK_AUTO),
            FieldSpec("phone", "연락처", "예: 내선 2345, 02-555-0182(외부 연락처만)", ask=ASK_AUTO),
            FieldSpec("note", "비고(유의사항)", "연락할 때 주의할 점", ask=ASK_AUTO),
            FieldSpec("scope", "구분", "내부(우리 기관 소속) 또는 외부(다른 기관·업체) 중 하나", ask=ASK_HIDDEN),
        ),
        min_items=1,
        modes=(MODE_LEAVE,),
        queries=("담당자 연락처 내선 전화", "비상 연락망 문의처", "협업 부서 업체 담당자"),
        keywords=("연락", "연락처", "내선", "전화", "담당자", "문의", "메일"),
        default_question="휴가 중 업무와 관련해 연락할 사람이 있다면 소속·이름·연락처와 어떤 일로 연락하는지 알려 주시겠어요?",
        title_key="name",
        empty_ask=ASK_AUX,
        split_field="scope",
        split_drop=(("내부", ("phone",)),),
    ),
    # ------------------------------------------------------------------ 두 버전 공통: 기타 자료 — 시스템 접근 권한([인사발령] D / [장기휴가] C)
    SlotSpec(
        key="systems",
        title="시스템 접근 권한",
        description="업무에 쓰는 시스템·도구, 용도, 필요한 권한 수준과 권한 신청·이관 방법. 묻지 않고 자료에 있을 때만 채운다. ID/PW 는 보안상 AI 에 입력하지 않고 문서에서 수기로 적는다.",
        fields=(
            FieldSpec("name", "시스템", ask=ASK_AUTO),
            FieldSpec("purpose", "용도", ask=ASK_AUTO),
            FieldSpec("access", "권한 수준", "관리자/편집자/조회 등", ask=ASK_AUTO),
            FieldSpec("how_to_get", "신청·이관 방법", ask=ASK_AUTO),
        ),
        min_items=1,
        modes=BOTH,
        queries=("사용 시스템 접근 권한 계정", "권한 신청 방법 이관 승인", "관리자 계정 콘솔 접속"),
        keywords=("시스템", "권한", "계정", "콘솔", "관리자", "접속", "CMS", "로그인"),
        default_question="업무에 사용하는 시스템과, 인수자가 권한을 받으려면 어떻게 신청해야 하는지 알려 주시겠어요?\n(ID/PW 는 적지 마세요)",
        handwritten=("ID / PW (수기 기재)",),
        group=OTHER_MATERIALS,
        muted_note="시스템 접근 권한이 있을 경우 해당 내용을 작성해주세요.",
    ),
    # ------------------------------------------------------------------ [인사발령] 기타 자료 — 그 밖의 자료(부서별 추가 자료)
    SlotSpec(
        key="dept_notes",
        title="부서별 추가 자료",
        description="부서 업무의 특성상 반드시 챙겨야 하는 사항(외주 관리, 가맹점 관리, 라이선스 등)과 근거가 되는 사무인수인계서 사무목록·세부시행세칙. "
        "소속 부·팀의 인계 서류·물품 정보가 있으면 인계자의 업무 단위와 관련된 것만 항목으로 만든다. 이 장은 질의응답으로 묻지 않고 자료에서만 채운다(노션 '추가 요구사항 정의' 14).",
        fields=(
            FieldSpec("topic", "추가 자료", "예: 가맹점 관리, 외주 관리, 라이선스", ask=ASK_AUTO),
            FieldSpec("detail", "관리 내용", "무엇을 어떻게 챙겨야 하는지", ask=ASK_AUTO),
            FieldSpec("basis", "관련 규정·자료", "사무인수인계서 사무목록·세부시행세칙 등", ask=ASK_AUTO),  # 문서 표의 근거 번호 열('근거')과 이름이 겹치지 않게
        ),
        min_items=1,
        queries=("부서별 추가 자료 외주 관리 가맹점 관리", "라이선스 라이센스 관리 계약", "사무인수인계서 사무목록 세부시행세칙"),
        keywords=("외주", "가맹점", "라이선스", "라이센스", "위탁", "특이", "지침", "세칙", "사무목록"),
        default_question="소속 부서만의 추가 관리 사항(외주 관리, 가맹점 관리, 라이선스 등)이 있나요?\n없으면 '해당 없음'으로 건너뛰어 주세요.",
        group=OTHER_MATERIALS,
    ),
)

# 팀장용 원내 양식의 장(인사발령·장기휴가 양식에는 들어가지 않는다). 문서 순서: 1 사업계획 → 2 현안 → 3 제계약서 → 4 미결 → 5~15 → 16
OFFICE_PLANS = SlotSpec(
    key="office_plans",
    title="주요 사업계획 및 집행상황표",
    description="팀장이 인수인계를 시작할 때 고른 주요 사업계획(경영정보시스템 주요 업무보고 단위, 예: 은행 공동 본인확인 플랫폼 구축 추진)과 "
    "사업계획별 계획기간(월별 일정)·비고. 항목은 고른 사업계획만 쓰고 이름은 고른 그대로 둔다. 계획기간은 사업계획서·업무보고·추진계획에서 읽고, "
    "비고에는 지금까지의 진행상황을 1~2문장, 공백 포함 80자 안팎으로 짧게 요약한다(핵심 성과·현재 단계·다음 단계만).",
    fields=(
        FieldSpec("name", "주요 사업계획", "예: 은행 공동 본인확인 플랫폼 구축 추진", ask=ASK_AUTO),
        # 계획기간은 묻지 않는다: 자료에서 읽히면 채우고, 못 읽으면 비워 둔다(최종 문서의 일정표에서 막대를 끌어 직접 고른다)
        FieldSpec("period", "계획기간", "예: 2026.03~2026.12, 2026.11~2027.02, 연중 상시", ask=ASK_AUTO),
        FieldSpec("note", "비고", "이 사업계획은 지금 어디까지 진행되었는가?(진행상황 요약)"),
    ),
    min_items=1,
    modes=(),
    queries=("주요 사업계획 업무보고 추진 실적", "사업계획 추진 일정 기간", "추진 현황 진행 상황"),
    keywords=("사업계획", "추진", "계획", "기간", "일정", "실적", "현황", "진행"),
    default_question="",
    layout="gantt",
    evidence=False,
    seed="plans",
    muted_note="실제 업무 추진 현황 및 예상일정을 막대 그래프로 나타내주세요",
)
OFFICE_ISSUES = SlotSpec(
    key="office_issues",
    title="주요 현안사항",
    description="팀장이 현안이 있다고 고른 사업계획과 직접 추가한 현안. 현안마다 가. 개요(무엇이고 왜 현안인지), 나. 진행상황(지금까지의 경과), "
    "다. 고려사항(후임 관리자가 특별히 알아야 할 점)을 근거 자료에서 찾아 개괄식으로 짧게 쓴다(여럿이면 ' / '로 이어 씀). 항목은 고른 현안만 쓴다.",
    fields=(
        FieldSpec("name", "현안", ask=ASK_AUTO),
        FieldSpec("summary", "개요", "이 현안은 무엇이고 왜 현안인가?", ask=ASK_AUTO),
        FieldSpec("progress", "진행상황", "지금까지 어떤 진행 경과가 있었는가?"),
        FieldSpec("considerations", "고려사항", "후임 관리자가 특별히 알아야 할 사항은 무엇인가?"),
    ),
    min_items=0,
    modes=(),
    queries=("현안 이슈 쟁점 문제", "진행 경과 추진 현황", "유의사항 고려사항 리스크"),
    keywords=("현안", "이슈", "쟁점", "경과", "진행", "협의", "유의", "고려"),
    default_question="",
    layout="card",
    seed="issues",
)
OFFICE_PENDING = SlotSpec(
    key="office_pending",
    title="주요 미결사항의 경과 및 처리대책",
    description="현재까지 처리되지 않았거나 후임자가 이어서 처리해야 할 주요 업무(처리 지연, 검토·협의·승인 미완료). 미결사항마다 경과(지금까지 진행된 절차·조치)와 "
    "처리대책(해결하려면 앞으로 필요한 조치)을 쓴다. 이 장은 자료에서 채우지 않고 인계자에게 묻는 질문(<1> 미결 업무가 있는지 → 있으면 <2> 경과·조치)의 답으로만 채운다.",
    fields=(
        FieldSpec("name", "미결사항", "처리되지 않았거나 이어서 처리해야 할 업무", ask=ASK_AUTO),
        FieldSpec("progress", "경과", "지금까지 어떤 절차나 조치가 진행되었는가?"),
        FieldSpec("plan", "처리대책", "해결하기 위해 앞으로 어떤 조치가 필요한가?", ask=ASK_AUTO),
    ),
    min_items=0,
    modes=(),
    queries=("미결 지연 보류 승인 대기", "처리 대책 후속 조치 검토 협의"),
    keywords=("미결", "지연", "보류", "대기", "검토", "협의", "승인", "후속"),
    default_question=OFFICE_PENDING_QUESTION,
    layout="card",
    empty_ask=ASK_MAIN,
)
OFFICE_SLOT = SlotSpec(
    key="office_items",
    title="원내 인수인계서 양식",
    description="팀장이 쓰는 원내 양식의 인수인계 목록(직전 인수인계서와 첨부 자료에서만 산출, 질문하지 않음). 항목은 아래 정해진 목록이며 항목 제목·목록(name)에는 목록 이름을 번호까지 글자 그대로 쓴다: "
    + ", ".join(name for name, _ in OFFICE_ITEMS)
    + ". 목록별 작성 참고사항(문서에는 싣지 않는다): "
    + "; ".join(f"{name} — {note}" for name, note in OFFICE_ITEMS if note)
    + ". 각 목록에 해당하는 서류·현황·내용을 근거 자료에서 찾아 '내용'에 개괄식으로 짧게 적고(여럿이면 ' / '로 이어 씀) 근거 ID 를 붙인다. "
    "자료에서 하나라도 찾은 목록은 모두 항목으로 만들고, 찾지 못한 목록만 비워 둔다. 목록에 없는 항목은 만들지 않는다. 질문은 만들지 않는다.",
    fields=(
        FieldSpec("name", "목록", ask=ASK_AUTO),
        FieldSpec("content", "내용", "이 목록에 해당하는 서류·현황·내용(자료에서 찾은 것)", ask=ASK_AUTO),
        FieldSpec("note", "비고", "원내 양식의 비고(AI 가 내용을 쓸 때 참고하는 사항, 문서에는 싣지 않음)", ask=ASK_HIDDEN),
    ),
    min_items=1,
    modes=(),
    queries=("계약서 명세 계약 현황", "인장 보관 문서 목록 전산기기 보유현황", "고정자산 비품 보안업무 법인카드", "담당 규정 실무지침 매뉴얼 체크리스트"),
    keywords=("계약", "인장", "문서", "전산", "자산", "비품", "보안", "카드", "규정", "지침", "매뉴얼", "체크리스트"),
    default_question="",
    seed="office",
)
OFFICE_SLOTS: tuple[SlotSpec, ...] = (OFFICE_PLANS, OFFICE_ISSUES, OFFICE_PENDING, OFFICE_SLOT)

SLOT_BY_KEY: dict[str, SlotSpec] = {s.key: s for s in (*SLOTS, *OFFICE_SLOTS)}
SLOT_KEYS: tuple[str, ...] = tuple(s.key for s in (*SLOTS, *OFFICE_SLOTS))
ALL_FIELD_KEYS: tuple[str, ...] = tuple(dict.fromkeys(f.key for s in (*SLOTS, *OFFICE_SLOTS) for f in s.fields))


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
    """이번 인수인계서에 쓰는 슬롯(버전별로 장 구성이 다르다). 팀장이 쓰는 인수인계서(form=office)는 원내 양식의 장을 쓴다."""
    if is_office(profile):
        return OFFICE_SLOTS
    mode = mode_of(profile)
    # 부서별 추가 자료는 분석·질문하지 않고 인계자가 항목별로 파일만 올린다(render 가 업로드 표로 그린다, 노션 '추가 요구사항 정의')
    return tuple(s for s in SLOTS if mode in s.modes and s.key != "dept_notes")


def is_office(profile: dict[str, Any] | None) -> bool:
    """팀장이 쓰는 인사발령 인수인계서(원내 양식)인지. 팀장의 장기휴가는 실무자와 같은 양식으로 쓴다(노션 '추가 요구사항 정의' 관리자용 6)."""
    return str((profile or {}).get("form") or "").strip().lower() == FORM_OFFICE and mode_of(profile) != MODE_LEAVE


def profile_units(profile: dict[str, Any] | None) -> list[dict[str, Any]]:
    """인계자가 고른 단위업무와 단위업무별 세부업무: [{"name", "regular": [...], "irregular": [...]}] (빈 이름·빈 값은 뺀다).

    인사발령은 직제규정 시행세칙에서 고른 단위업무마다 정기/비정기 세부업무를 입력하고, 장기휴가는 이름 없는 단위업무 하나에 세부업무만 담는다.
    """
    out: list[dict[str, Any]] = []
    for raw in (profile or {}).get("units") or []:
        if not isinstance(raw, dict):
            continue
        name = " ".join(str(raw.get("name") or "").split())
        tasks = {kind: [" ".join(str(t).split()) for t in raw.get(kind) or [] if str(t).strip()] for kind in ("regular", "irregular")}
        if name or tasks["regular"] or tasks["irregular"]:
            out.append({"name": name, **tasks})
    return out


def _names(values: Any) -> list[str]:
    out: list[str] = []
    for raw in values if isinstance(values, list) else []:
        name = " ".join(str((raw.get("name") if isinstance(raw, dict) else raw) or "").split())
        if name and name not in out:
            out.append(name)
    return out


def profile_plans(profile: dict[str, Any] | None) -> list[str]:
    """팀장이 고른 주요 사업계획 이름(원내 양식 1번의 행)."""
    return _names((profile or {}).get("plans"))


def profile_issues(profile: dict[str, Any] | None) -> list[str]:
    """팀장이 현안이 있다고 고른 사업계획과 직접 추가한 현안(원내 양식 2번의 항목)."""
    return _names((profile or {}).get("issues"))


def seed_items(spec: SlotSpec, profile: dict[str, Any] | None) -> list[dict[str, str]]:
    """인계자 정보로 미리 만드는 항목의 필드: 단위업무(업무 단위 요약) · 세부업무(정기/비정기, 장기휴가 세부 업무) · 팀장의 사업계획·현안."""
    if spec.seed == "plans":
        return [{"name": name} for name in profile_plans(profile)]
    if spec.seed == "issues":
        return [{"name": name} for name in profile_issues(profile)]
    units = profile_units(profile)
    if spec.seed == "units":
        return [{"name": u["name"]} for u in units if u["name"]]
    if spec.seed in ("regular", "irregular"):
        return [{"name": task, "unit": u["name"]} for u in units for task in u[spec.seed]]
    if spec.seed == "office":
        return [{"name": name, "note": note} for name, note in OFFICE_ITEMS]
    if spec.seed == "leave":
        return [{"name": task} for kind in ("regular", "irregular") for u in units for task in u[kind]]  # 장기휴가는 정기/비정기로 나누지 않는다
    return []


def has_seeds(profile: dict[str, Any] | None) -> bool:
    """인계자가 단위업무·세부업무(팀장은 사업계획·현안)를 입력했는지(입력했으면 장의 항목은 입력한 것만 쓴다)."""
    return bool(profile_units(profile) or profile_plans(profile) or profile_issues(profile))


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
