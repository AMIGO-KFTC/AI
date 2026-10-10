"""규칙 기반 오프라인 엔진.

API 키가 없어도 STAGE 1~4 전체 흐름을 시연할 수 있도록, 검색 결과의 표(머리글 매핑)와 문장(키워드·정규식)에서
항목을 뽑고, 필수 필드가 빈 곳을 질문으로 만든다. 품질은 Claude 엔진보다 낮지만 결과는 항상 결정적이다.
"""

from __future__ import annotations

import re
from typing import Any

from ..config import AgentConfig
from ..kb import Evidence, KBAdapter
from ..state import Gap, SlotItem, SlotState, evaluate_coverage, item_label, missing_fields, new_id, upsert_item
from ..template import COVERAGE_LABELS, SLOT_BY_KEY, SLOTS, SlotSpec
from ..textkit import (
    extract_contacts,
    extract_parties,
    extract_person,
    infer_cycle,
    josa,
    short_title,
    strip_list_mark,
)
from . import Interpretation, InterpretInput, SlotResult, retrieve_for_slot

# 표 머리글 → 슬롯 필드 매핑
HEADER_SYNONYMS: dict[str, dict[str, tuple[str, ...]]] = {
    "overview": {
        "name": ("단위업무명", "업무명", "담당업무", "업무"),
        "summary": ("업무소개", "주요내용", "업무내용", "내용", "설명"),
        "guideline": ("업무지침", "지침"),
        "law": ("관계법령", "법령", "근거법"),
        "manual": ("업무매뉴얼", "매뉴얼"),
    },
    "stakeholders": {
        "party": ("부서", "기관", "업체", "소속", "협업대상", "이해관계자"),
        "scope": ("구분", "내외부"),
        "relation": ("관계유형", "관계"),
        "person": ("담당자", "성명", "이름", "담당"),
        "contact": ("연락처", "전화", "내선", "이메일", "메일"),
        "detail": ("협의내용", "협업내용", "역할", "업무"),
    },
    "regular": {
        "name": ("세부업무명", "업무명", "업무", "작업"),
        "intro": ("소개", "설명"),
        "cycle": ("주기", "빈도"),
        "timing": ("시기", "일정", "기한", "시점", "월"),
        "procedure": ("수행절차", "처리절차", "절차", "방법"),
        "key_points": ("중요사항", "유의사항", "중요하게"),
        "decisions": ("주요결정", "결정사항"),
        "consultations": ("협의사항", "주요협의"),
        "expectations": ("후임자", "바라는"),
        "tips": ("팁", "참고자료"),
    },
    "irregular": {
        "name": ("세부업무명", "과제명", "과제", "프로젝트", "사업명"),
        "background": ("배경", "소개", "추진배경"),
        "goal": ("목표", "목적"),
        "progress": ("진행경과", "진행현황", "현황", "진행상황", "상태", "진척"),
        "schedule": ("진행일정", "일정", "마감", "기한", "완료예정"),
        "consultations": ("협의사항", "협의내용"),
        "open_issues": ("미결과제", "미결", "다음할일", "향후계획", "조치사항", "다음단계", "할일"),
        "key_points": ("중요사항", "유의사항"),
        "decisions": ("주요결정", "결정사항"),
        "expectations": ("후임자", "바라는"),
    },
    "systems": {
        "name": ("시스템명", "시스템", "도구"),
        "purpose": ("용도", "목적", "기능"),
        "access": ("권한수준", "권한", "계정유형"),
        "how_to_get": ("신청이관방법", "신청방법", "이관방법", "신청", "이관", "비고"),
    },
    "dept_notes": {
        "topic": ("특이사항", "항목", "구분", "관리대상"),
        "detail": ("관리내용", "내용", "관리방법", "유의사항"),
        "basis": ("근거지침", "근거", "지침"),
    },
}

# 표가 해당 슬롯의 표로 인정되려면 제목 열 외에 이 중 하나 이상의 열이 있어야 한다
COMPANION_FIELDS: dict[str, set[str]] = {
    "overview": {"summary", "guideline", "law", "manual"},
    "stakeholders": {"person", "contact", "detail", "relation"},
    "regular": {"cycle", "timing", "procedure"},
    "irregular": {"background", "goal", "progress", "schedule", "open_issues"},
    "systems": {"purpose", "access", "how_to_get"},
    "dept_notes": {"detail", "basis"},
}

SENTENCE_FALLBACK_ONLY = {"overview", "systems", "irregular", "dept_notes"}

DEPT_HINTS = re.compile(r"외주|가맹점|라이선스|라이센스|위탁|특이사항|세부시행세칙")
EXTERNAL_HINTS = re.compile(r"업체|기관|공단|은행|협회|공사|위원회|금융위|\(주\)|㈜|주식회사|외부")
RELATION_RE = re.compile(r"요청|협의|보고|문의|통보|승인|협조")
PROJECT_HINTS = re.compile(r"추진|예정|진행 중|진행중|검토 중|입찰|작성 중")
SYSTEM_NAME_RE = re.compile(r"([A-Z][A-Za-z0-9]{1,15}(?:\s[A-Z][A-Za-z0-9]+)?|[가-힣A-Za-z]+시스템|[가-힣A-Za-z]+콘솔)")
AFFIRM_RE = re.compile(r"^\s*(네|예|응|넵|맞아|맞습니다|맞아요|맞음|ㅇㅇ|좋아|좋습니다|좋아요|확인|그래|오케이|ok|yes)", re.I)
SKIP_RE = re.compile(r"(모르겠|몰라|건너|스킵|skip|패스|다음 질문|해당\s*없|^없(어요|습니다|음)?\s*$)", re.I)
FINISH_RE = re.compile(r"(문서|인수인계서)\s*(를|을)?\s*(생성|작성|만들)|그만|종료|끝내|마무리")
NEGATIVE_RE = re.compile(r"^\s*(아니(?:요|오|에요)?|아뇨|틀렸(?:어요|습니다)?|틀려요?|수정(?:해\s*주세요|할게요)?)")


def infer_scope(party: str, context: str = "") -> str:
    """부서·기관 이름과 문맥으로 내부/외부를 추정한다(교차 확인 단계에서 인계자가 바로잡는다)."""
    if "외부" in context:
        return "외부"
    if "내부" in context:
        return "내부"
    return "외부" if EXTERNAL_HINTS.search(party) else "내부"


def infer_relation(text: str) -> str:
    match = RELATION_RE.search(text or "")
    return "협의" if not match or match.group(0) == "협조" else match.group(0)


def _norm(text: str) -> str:
    return re.sub(r"[\s/·()\[\]]+", "", text or "")


class OfflineEngine:
    name = "offline"

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------ STAGE 1
    def analyze_slot(self, spec: SlotSpec, profile: dict[str, Any], kb: KBAdapter) -> SlotResult:
        evidences = retrieve_for_slot(spec, profile, kb, top_k=self.config.search_top_k)
        slot: SlotState = {"key": spec.key, "items": [], "coverage": "missing", "summary": "", "note": ""}
        # 대화 답변은 이미 슬롯에 반영되어 있으므로 규칙 기반 추출 대상에서 뺀다(질문 문장이 섞여 오인식됨)
        usable = [ev for ev in evidences if ev.score >= 0.2 and "chat" not in (ev.metadata.get("source_type"), ev.metadata.get("file_type"))]
        # 1차: 표(머리글 매핑)  2차: 문장. 담당 업무·시스템·과제는 문장 추출이 부정확해 표가 부족할 때만 쓴다.
        for ev in usable:
            for title, fields in self._table_candidates(spec, ev.text):
                self._add(slot, title, fields, ev)
        if spec.key not in SENTENCE_FALLBACK_ONLY or len(slot["items"]) < spec.min_items:
            for ev in usable:
                for title, fields in self._sentence_candidates(spec, ev, profile):
                    self._add(slot, title, fields, ev)
        items = slot["items"]
        slot["coverage"] = evaluate_coverage(spec, items)
        slot["summary"] = self._slot_summary(spec, items)
        return SlotResult(slot=slot, gaps=self._gaps(spec, items, slot["coverage"]))

    def _add(self, slot: SlotState, title: str, fields: dict[str, str], ev: Evidence) -> None:
        if len(slot["items"]) >= 8 and not any(_norm(i.get("title", "")) == _norm(title) for i in slot["items"]):
            return
        upsert_item(slot, item_id="", title=title, fields=fields, citations=[ev.to_citation(title)], origin="document")

    def _table_candidates(self, spec: SlotSpec, text: str):
        lines = text.split("\n")
        i = 0
        while i < len(lines):
            if lines[i].startswith("|") and i + 1 < len(lines) and re.match(r"^\|(-+\|)+$", lines[i + 1].replace(" ", "")):
                header = [c.strip() for c in lines[i].strip("|").split("|")]
                mapping = self._map_header(spec.key, header)
                j = i + 2
                usable = spec.title_field in mapping.values() and bool(set(mapping.values()) & COMPANION_FIELDS[spec.key])
                while j < len(lines) and lines[j].startswith("|"):
                    if usable:
                        cells = [c.strip() for c in lines[j].strip("|").split("|")]
                        fields = {key: cells[idx] for idx, key in mapping.items() if idx < len(cells) and cells[idx] not in ("", "-")}
                        if spec.key == "regular":
                            cycle = fields.pop("cycle", "") or infer_cycle(fields.get("timing", ""), fields.get("name", ""))
                            timing = fields.get("timing", "")
                            if cycle and cycle not in timing:
                                fields["timing"] = f"{cycle} {timing}".strip()
                        title = fields.get(spec.title_field, "")
                        if title:
                            yield title, fields
                    j += 1
                i = j
            else:
                i += 1

    def _map_header(self, slot_key: str, header: list[str]) -> dict[int, str]:
        mapping: dict[int, str] = {}
        synonyms = HEADER_SYNONYMS.get(slot_key, {})
        for idx, cell in enumerate(header):
            norm = _norm(cell)
            for field, words in synonyms.items():
                if field in mapping.values():
                    continue
                if any(norm == w or (len(w) >= 2 and w in norm) for w in words):
                    mapping[idx] = field
                    break
        return mapping

    def _sentence_candidates(self, spec: SlotSpec, ev: Evidence, profile: dict[str, Any]):
        sentences: list[str] = []
        for block in re.split(r"\n\s*\n", ev.text):
            lines = [ln for ln in block.split("\n") if ln.strip() and not ln.lstrip().startswith(("|", "#"))]
            if not lines:
                continue
            # 목록(가. 나. / - )은 줄 단위, 일반 문단은 줄을 이어 붙여 문장 단위로 나눈다
            if all(strip_list_mark(ln) != ln.strip() for ln in lines):
                units = [strip_list_mark(ln) for ln in lines]
            else:
                units = re.split(r"(?<=다\.)\s+|(?<=[!?])\s+", " ".join(ln.strip() for ln in lines))
            sentences += [u.strip() for u in units if len(u.strip()) >= 8]
        produced = 0
        for sentence in sentences:
            for candidate in self._sentence_to_items(spec, sentence, ev, profile):
                if produced >= 4:
                    return
                produced += 1
                yield candidate

    def _sentence_to_items(self, spec: SlotSpec, sentence: str, ev: Evidence, profile: dict[str, Any]):
        key = spec.key
        if key == "regular":
            cycle = infer_cycle(sentence)
            if not cycle or not re.search(r"매일|매주|매월|매년|분기|정기|영업일|월간|주간|연간", sentence):
                return
            timing, _, name = sentence.partition(":")
            if not name:
                name, timing = sentence, ""
            timing = timing.strip()
            if cycle not in timing:
                timing = f"{cycle} {timing}".strip()
            yield short_title(name, 40), {"name": short_title(name, 60), "timing": timing, "procedure": name.strip()}
        elif key == "stakeholders":
            if not re.search(r"협의|협업|요청|연락|문의|관리|보고", sentence):
                return
            own = str(profile.get("organization") or "")
            sender = str(ev.metadata.get("sender_name") or "")
            clauses = [c.strip() for c in re.split(r",\s*|\s(?=[가-힣A-Za-z0-9]+(?:팀|부서|업체)(?:과는|와는|과|와)\s)", sentence) if c.strip()]
            for party in extract_parties(sentence):
                if party in own or party == "업체":
                    continue
                clause = next((c for c in clauses if party in c), sentence)
                person = extract_person(clause) or (sender if sender and sender in clause else "")
                contact = extract_contacts(clause)
                if sender and person.startswith(sender):
                    contact = contact or extract_contacts(str(ev.metadata.get("sender", "")))
                detail = re.sub(rf"^.*?{re.escape(party)}(\([^)]*\))?(과는|와는|과|와|에)?\s*", "", clause).strip(" ,.") or clause
                yield party, {
                    "party": party,
                    "scope": infer_scope(party),
                    "relation": infer_relation(clause),
                    "person": person,
                    "detail": short_title(detail, 80),
                    "contact": contact,
                }
        elif key == "dept_notes":
            if DEPT_HINTS.search(sentence):
                hint = DEPT_HINTS.search(sentence).group(0)
                yield short_title(sentence, 30), {"topic": hint, "detail": sentence}
        elif key == "irregular":
            if PROJECT_HINTS.search(sentence) and not sentence.startswith(("또한", "그리고")):
                yield short_title(sentence, 30), {"name": short_title(sentence, 40), "progress": sentence}
        elif key == "systems":
            match = SYSTEM_NAME_RE.search(sentence)
            if match and re.search(r"권한|계정|콘솔|접속|로그인", sentence):
                yield match.group(1), {"name": match.group(1), "purpose": sentence}
        elif key == "overview":
            if re.search(r"담당한다|담당하며|수행한다|담당 업무", sentence) and re.search(r"업무|관리|운영", sentence):
                yield short_title(sentence, 30), {"name": short_title(sentence, 40), "summary": sentence}

    def _slot_summary(self, spec: SlotSpec, items: list[SlotItem]) -> str:
        if not items:
            return f"자료에서 {spec.title} 정보를 찾지 못했습니다."
        names = ", ".join(item_label(spec, it) for it in items[:3])
        more = f" 외 {len(items) - 3}건" if len(items) > 3 else ""
        return f"{names}{more}"

    def _gaps(self, spec: SlotSpec, items: list[SlotItem], coverage: str) -> list[Gap]:
        priority = 1 if spec.key in ("stakeholders", "systems") else 2
        if coverage == "missing":
            return [
                {
                    "id": new_id("gap"),
                    "slot": spec.key,
                    "item_id": "",
                    "description": f"자료에서 {spec.title} 정보를 찾지 못함",
                    "question": spec.default_question,
                    "priority": priority,
                    "ask_for_document": spec.key in ("systems", "stakeholders"),
                    "status": "open",
                }
            ]
        gaps: list[Gap] = []
        for item in items:
            missing = missing_fields(spec, item)
            if not missing:
                continue
            label = item_label(spec, item)
            names = ", ".join(spec.label(m) for m in missing)
            gaps.append(
                {
                    "id": new_id("gap"),
                    "slot": spec.key,
                    "item_id": item["id"],
                    "description": f"'{label}'의 {names} 정보 없음",
                    "question": f"자료에 있는 '{label}' 관련해서 {josa(names, '을/를')} 알려 주시겠어요?",
                    "priority": priority,
                    "ask_for_document": False,
                    "status": "open",
                }
            )
            if len(gaps) >= self.config.max_gaps_per_slot:
                break
        return gaps

    # ------------------------------------------------------------------ STAGE 2
    def summarize(self, profile, slots, gaps, sources) -> str:
        filled = [SLOT_BY_KEY[k].title for k, s in slots.items() if s.get("coverage") == "sufficient"]
        weak = [SLOT_BY_KEY[k].title for k, s in slots.items() if s.get("coverage") != "sufficient"]
        text = f"올려 주신 자료 {len(sources)}건을 분석했어요."
        if filled:
            text += f" {', '.join(filled)} 항목은 자료만으로 충분히 파악했습니다."
        if weak:
            text += f" {', '.join(weak)} 항목은 보완이 필요해요."
        return text

    # ------------------------------------------------------------------ STAGE 3
    def interpret(self, data: InterpretInput, kb: KBAdapter) -> Interpretation:
        text = data.message.strip()
        compact = re.sub(r"\s+", "", text)
        if FINISH_RE.search(text):
            return Interpretation("finish", reply="네, 지금까지 정리한 내용으로 인수인계서를 작성할게요.")
        if data.pending and AFFIRM_RE.match(text) and len(compact) <= 12:
            return Interpretation("confirm", resolved_gap_ids=[data.pending.get("gap_id", "")], reply="확인했습니다.")
        if data.stage == "review" and AFFIRM_RE.match(text) and len(compact) <= 12:
            return Interpretation("confirm", reply="좋습니다. 인수인계서를 PDF나 Word 로 내려받으실 수 있어요.")
        if SKIP_RE.search(text) and len(compact) <= 15:
            return Interpretation("skip", reply="괜찮습니다. 이 부분은 '확인 필요 사항'으로 남겨 둘게요.")
        if data.pending and NEGATIVE_RE.match(text) and len(compact) <= 6:
            return Interpretation("correct", reply="어떤 부분을 어떻게 고치면 될지 말씀해 주세요.")

        gap = data.current_gap
        correcting = bool(data.pending) and bool(NEGATIVE_RE.match(text))
        if correcting:
            text = NEGATIVE_RE.sub("", text).lstrip(" ,.").strip() or text
        slot_key = gap.get("slot") if gap else self._guess_slot(text)
        spec = SLOT_BY_KEY[slot_key]
        slot = data.slots.get(slot_key) or {"key": slot_key, "items": []}
        target = None
        if correcting and data.pending and data.pending.get("refs"):
            ref_slot, ref_id = data.pending["refs"][0]
            spec = SLOT_BY_KEY[ref_slot]
            slot = data.slots.get(ref_slot) or {"key": ref_slot, "items": []}
            target = next((it for it in slot.get("items", []) if it.get("id") == ref_id), None)
        elif gap and gap.get("item_id"):
            target = next((it for it in slot.get("items", []) if it.get("id") == gap["item_id"]), None)

        if correcting and target is not None:
            keys = (data.pending or {}).get("keys", {}).get(target["id"]) or missing_fields(spec, target) or [spec.fields[1].key]
            fields = self._correction_fields(spec, text, keys)
        else:
            fields = self._fields_from_answer(spec, text, target)
        title = item_label(spec, target) if target else short_title(text, 24)
        update = {"slot": spec.key, "item_id": target["id"] if target else "", "title": title, "fields": fields, "citations": []}
        lines = [f"- **{spec.label(k)}**: {v}" for k, v in fields.items() if v]
        reply = (
            f"다음과 같이 정리했어요.\n\n**[{spec.title}] {title}**\n" + "\n".join(lines)
            + "\n\n맞으면 '네', 고칠 부분이 있으면 말씀해 주세요."
        )
        return Interpretation("correct" if correcting else "answer", updates=[update], resolved_gap_ids=[gap["id"]] if gap else [], reply=reply)

    def _correction_fields(self, spec: SlotSpec, text: str, keys: list[str]) -> dict[str, str]:
        """정정 답변은 직전에 기록한 필드를 덮어쓴다(연락처·담당자는 해당 패턴만 추출)."""
        fields: dict[str, str] = {}
        for key in keys:
            if key == "contact":
                fields[key] = extract_contacts(text) or text
            elif key == "person":
                fields[key] = extract_person(text) or text
            elif key != spec.title_field:
                fields[key] = text
        if not fields and keys:
            fields[keys[0]] = text
        return fields

    def _guess_slot(self, text: str) -> str:
        best, best_hits = "overview", 0
        for spec in SLOTS:
            hits = sum(1 for kw in spec.keywords if kw in text)
            if hits > best_hits:
                best, best_hits = spec.key, hits
        return best

    def _fields_from_answer(self, spec: SlotSpec, text: str, target: SlotItem | None) -> dict[str, str]:
        missing = missing_fields(spec, target) if target else [f.key for f in spec.fields]
        fields: dict[str, str] = {}
        remaining = text
        if "contact" in missing:
            contact = extract_contacts(text)
            if contact:
                fields["contact"] = contact
        if "person" in missing:
            person = extract_person(text)
            if person:
                fields["person"] = person
        if "party" in missing:
            parties = extract_parties(text)
            if parties:
                fields["party"] = parties[0]
        if spec.key == "stakeholders":
            party = fields.get("party") or (target or {}).get("fields", {}).get("party", "") or (target or {}).get("title", "")
            if "scope" in missing and party:
                fields["scope"] = infer_scope(party, text)
            if "relation" in missing and party:
                fields["relation"] = infer_relation(text)
        if "timing" in missing and spec.key == "regular":
            cycle = infer_cycle(text)
            if cycle:
                fields["timing"] = f"{cycle} {text}".strip() if cycle not in text else text
        if not target:
            title = short_title(text, 40)
            fields.setdefault(spec.title_field, title)
        for key in missing:
            if key not in fields:
                fields[key] = remaining.strip()
                break
        if spec.key == "regular" and not fields.get("timing") and "timing" in missing:
            fields["timing"] = text
        return fields

    def plan_questions(self, profile, gaps) -> tuple[list[str], set[str]]:
        """규칙 엔진은 장별 우선순위를 그대로 쓴다."""
        return [g["id"] for g in gaps], set()

    # ------------------------------------------------------------------ STAGE 4
    def compose_extras(self, profile, slots) -> dict[str, Any]:
        name = profile.get("name") or "인계자"
        org = profile.get("organization") or ""
        duties = profile.get("duties") or "담당 업무"
        counts = {k: len(s.get("items", [])) for k, s in slots.items()}
        subject = name + (f" {profile['position']}" if profile.get("position") else "")
        overview = (
            f"{josa(subject, '은/는')} {org + '에서 ' if org else ''}"
            f"{josa(duties, '을/를')} 담당해 왔습니다. 정기 업무 {counts.get('regular', 0)}건, 비정기 업무 {counts.get('irregular', 0)}건, "
            f"이해관계자 {counts.get('stakeholders', 0)}곳과의 관계를 인계합니다."
        )
        key_points: list[str] = []
        for key in ("irregular", "regular"):
            spec = SLOT_BY_KEY[key]
            for item in slots.get(key, {}).get("items", [])[:2]:
                f = item.get("fields", {})
                when = f.get("schedule") or f.get("timing") or ""
                key_points.append(f"{item_label(spec, item)}" + (f" ({when})" if when else ""))
        return {"overview": overview, "key_points": key_points[:5]}


def coverage_line(spec: SlotSpec, slot: SlotState) -> str:
    coverage = slot.get("coverage", "missing")
    return f"{spec.title}: {COVERAGE_LABELS.get(coverage, coverage)} ({len(slot.get('items', []))}건)"


__all__ = ["OfflineEngine", "coverage_line"]
