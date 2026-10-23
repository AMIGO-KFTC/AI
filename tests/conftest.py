import re
import uuid

import pytest

DOCS = [
    {
        "file_name": "업무정의서.pdf",
        "source_type": "pdf",
        "page": 1,
        "text": "## 2. 단위 업무\n\n| 단위업무명 | 계획기간 | 비고 |\n|---|---|---|\n"
        "| 홈페이지 콘텐츠 관리 | 상시 | 공지사항 게시, 메뉴 관리 |\n| 유지보수 계약 관리 | 매월 | 업체 관리, 월간 실적 검수 |",
    },
    {
        "file_name": "업무정의서.pdf",
        "source_type": "pdf",
        "page": 1,
        "text": "## 3. 반복 수행 업무\n\n가. 매월 5영업일 이내: 유지보수 월간 실적 검수 후 재무팀에 대금 지급 요청\n나. 매년 11월: 웹 접근성 품질인증 갱신 신청",
    },
    {
        "file_name": "업무정의서.pdf",
        "source_type": "pdf",
        "page": 2,
        "text": "## 4. 유관 부서\n\n재무팀(박지훈 차장)과 유지보수 대금 지급 및 리뉴얼 예산을 협의한다.",
    },
    {
        "file_name": "운영매뉴얼.hwpx",
        "source_type": "hwpx",
        "text": "| 시스템 | 용도 | 권한 수준 | 신청/이관 방법 |\n|---|---|---|---|\n"
        "| CMS | 게시물 등록·승인 | 관리자 | 전자결재 권한신청서 제출 |\n| Google Analytics | 방문 통계 | 편집자 |  |",
    },
    {
        "file_name": "회의록.docx",
        "source_type": "docx",
        "text": "| 과제 | 진행 현황 | 일정 | 다음 할 일 |\n|---|---|---|---|\n"
        "| 홈페이지 리뉴얼 | RFP 초안 80% 작성 | 10월 입찰 공고 예정 | 요구사항 정의서 보완 |\n\n"
        "- 리뉴얼 예산 3억 원은 이사회 승인을 받아야 확정된다(11월 예정).",
    },
]


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[가-힣]+|[A-Za-z0-9]+", text.lower())
    out = set(words)
    for w in words:
        if re.match(r"^[가-힣]{2,}$", w):
            out.update(w[i : i + 2] for i in range(len(w) - 1))
    return out


class FakeKB:
    """amigo_rag 없이 에이전트를 시험하기 위한 간단한 메모리 지식베이스."""

    def __init__(self, docs=None):
        self.docs = []
        for i, d in enumerate(docs if docs is not None else DOCS):
            meta = {k: v for k, v in d.items() if k != "text"}
            meta.setdefault("source_id", d["file_name"])
            self.docs.append({"chunk_id": f"c{i}", "text": d["text"], "metadata": meta})
        self.added = []

    def search(self, query, top_k=5, **_):
        q = _tokens(query)
        scored = []
        for d in self.docs:
            overlap = len(q & _tokens(d["text"] + " " + d["metadata"].get("file_name", "")))
            if overlap:
                scored.append((overlap / max(len(q), 1), d))
        scored.sort(key=lambda x: x[0], reverse=True)
        out = []
        for score, d in scored[:top_k]:
            meta = d["metadata"]
            label = meta["file_name"] + (f" p.{meta['page']}" if meta.get("page") else "")
            out.append({"chunk_id": d["chunk_id"], "text": d["text"], "metadata": meta, "score": min(1.0, 0.3 + score), "citation": label})
        return out

    def add_text(self, text, source_name, source_type="chat", metadata=None):
        sid = f"chat-{uuid.uuid4().hex[:6]}"
        self.docs.append(
            {"chunk_id": sid, "text": text, "metadata": {"source_id": sid, "file_name": source_name, "source_type": source_type}}
        )
        self.added.append(text)
        return {"source_id": sid}

    def list_sources(self):
        seen = {}
        for d in self.docs:
            m = d["metadata"]
            seen.setdefault(m["source_id"], {"source_id": m["source_id"], "source_name": m["file_name"], "source_type": m["source_type"], "chunk_count": 0})
            seen[m["source_id"]]["chunk_count"] += 1
        return list(seen.values())


PROFILE = {
    "name": "김민수",
    "position": "과장",
    "organization": "디지털전략부 웹서비스팀",
    "duties": "기관 홈페이지 운영",
    "successor": "이서연 대리",
    "handover_date": "2026-10-15",
}


@pytest.fixture()
def kb():
    return FakeKB()


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setenv("AMIGO_LLM_MODE", "offline")


# 양식의 장 순서(개요 → 정기 → 비정기 → 협업 → 시스템 → 부서)대로 질문하므로, 협업관계 질문부터 시험하려면 앞 장(정기·비정기)에 빈 필수 필드가 없어야 한다.
COMPLETE_CARDS = [
    {
        "file_name": "업무카드.xlsx",
        "source_type": "xlsx",
        # 비정기 업무 장도 같은 표를 읽으므로(세부업무명 → 프로젝트명) 그 장의 필수 열(목표·추진경과·향후추진과제)도 채워 둔다
        "text": "| 세부업무명 | 업무개요 | 수행시기 | 수행절차 | 중점관리사항 | 주요의사결정 | 목표 | 추진경과 | 향후추진과제 |\n|---|---|---|---|---|---|---|---|---|\n"
        "| 유지보수 월간 실적 검수 | 월간 실적 검수 | 매월 5영업일 이내 | 실적보고서 대조 후 지급 요청 | SLA 초과 건 확인 | SLA 1시간 기준 | 정시 지급 | 진행 중 | 검수 자동화 |",
    },
    {
        "file_name": "과제카드.xlsx",
        "source_type": "xlsx",
        "text": "| 프로젝트명 | 프로젝트개요 | 목표 | 추진경과 | 향후추진과제 |\n|---|---|---|---|---|\n"
        "| 홈페이지 리뉴얼 | 노후 홈페이지 개편 | 반응형 전환 | RFP 작성 중 | 업체 선정 |",
    },
]


@pytest.fixture()
def kb_ordered():
    """앞 장(정기·비정기 업무)에 빈 필수 필드가 없어 첫 질문이 협업관계 '프로젝트명'인 지식베이스."""
    return FakeKB([DOCS[0], DOCS[2], DOCS[3], *COMPLETE_CARDS])
