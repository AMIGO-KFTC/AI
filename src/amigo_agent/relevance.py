"""인계자의 답이 현재 질문과 상관있는지 코드로 한 번 더 검사한다(모델의 판단만 믿으면 엉뚱한 말이 항목으로 정리되기 때문).

3) is_explicit_skip: 모델이 '건너뛰기'로 판단해도, 인계자가 모른다·건너뛴다고 분명히 말했을 때만 건너뛴다('ㅇㅇ'·'음' 같은 애매한 말은 건너뛰지 않음)

보수적으로 걸러낸다: 질문과 무관하다고 확실한 말만 막고, 애매하면 통과시킨다(정상 답변을 막는 것이 더 나쁘다).
1) off_topic_reason: 의미 없는 글자·인사·잡담처럼 질문과 무관한 말인지(질문·항목과 겹치는 낱말, 숫자, 업무 낱말이 있으면 통과)
2) grounded_updates: 모델이 정리한 값이 인계자의 말에서 나온 값인지(말에 근거가 없고 자료 근거도 없는 값은 지어낸 것으로 보고 버린다)
"""

from __future__ import annotations

import re
from typing import Any

from .state import Gap
from .template import SLOT_BY_KEY

# 질문과 무관한 잡담·인사·시험 입력(짧은 말일 때만 본다)
CHATTER_RE = re.compile(
    r"안녕|반가워|하이\b|hello|\bhi\b|고마워|감사합니다|수고|점심|저녁|아침|날씨|배고|배불|졸려|피곤|심심|뭐해|뭐하|누구야|너는 누구|이름이 뭐|테스트|test|asdf|qwer|zzz|lol|하하|호호|히히|"
    r"커피|퇴근|주말|영화|게임|노래|사랑|농담",
    re.I,
)
NOISE_RE = re.compile(r"^[\sㅋㅎㅠㅜㅡㅇ.,?!~\-_/*+=()\[\]{}'\"`^&%$#@;:|\\<>]*$")
STOPWORDS = {"무엇", "어떻", "어떤", "알려", "주시", "있나", "있는", "하는", "되나", "인가", "경우", "대한", "관련", "해서", "그리고", "에서", "으로", "에게", "때문", "정도", "누구"}
SHORT_LIMIT = 30  # 이 길이 이하의 잡담 표현만 막는다(긴 답변 안의 우연한 낱말은 막지 않는다)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[가-힣A-Za-z0-9]{2,}", text or "") if w not in STOPWORDS}


# 어느 문장에나 붙는 끝맺음 글자쌍(입니다·습니다·해요 …)은 같은 말을 했다는 증거가 못 되므로 비교에서 뺀다.
ENDING_BIGRAMS = {"니다", "입니", "습니", "합니", "됩니", "있습", "없습", "했습", "었습", "였습", "아요", "어요", "여요", "해요", "세요", "에요", "예요", "이에", "이예", "이다", "했다", "한다", "된다", "있다", "없다"}


def bigrams(text: str) -> set[str]:
    chars = re.sub(r"[^가-힣A-Za-z0-9]", "", (text or "").lower())
    return {chars[i : i + 2] for i in range(len(chars) - 1)} - ENDING_BIGRAMS


def context_words(gap: Gap | None, item_title: str = "") -> set[str]:
    """현재 질문과 이어진 낱말: 질문·공백 설명·항목 이름·장 이름·필드 이름·장의 키워드."""
    if not gap:
        return set()
    spec = SLOT_BY_KEY.get(gap.get("slot", ""))
    parts = [gap.get("question", ""), gap.get("description", ""), item_title]
    words = set()
    if spec is not None:
        parts += [spec.title, *(f.label for f in spec.fields)]
        words |= {k for k in spec.keywords if len(k) >= 2}
    for part in parts:
        words |= _words(part)
    return words


def off_topic_reason(text: str, gap: Gap | None, item_title: str = "") -> str | None:
    """질문과 무관하다고 확실하면 이유를, 아니면 None. 질문이 없거나 아주 긴 말은 검사하지 않는다."""
    raw = " ".join((text or "").split())
    if gap is None or not raw:
        return None
    if NOISE_RE.match(raw):
        return "의미 있는 글자가 없음"
    if re.search(r"\d", raw):  # 날짜·연락처·금액·건수는 답일 가능성이 높다
        return None
    if len(raw) > SHORT_LIMIT:
        return None
    context = context_words(gap, item_title)
    overlaps = any(w in raw or any(t in w for t in _words(raw)) for w in context)
    if overlaps:
        return None
    if CHATTER_RE.search(raw):
        return "질문과 무관한 말"
    chars = re.sub(r"[^가-힣A-Za-z0-9]", "", raw)
    if len(chars) <= 3:  # 질문의 낱말도 숫자도 없는 아주 짧은 말('음', '그냥', 'ㅇㅋ')
        return "내용이 너무 짧음"
    return None


def grounded_updates(updates: list[dict[str, Any]], text: str) -> list[dict[str, Any]]:
    """모델이 정리한 값 중 인계자의 말(또는 자료 근거)에서 나온 값만 남긴다. 값이 하나도 안 남은 항목은 뺀다."""
    spoken = bigrams(text)
    kept: list[dict[str, Any]] = []
    for update in updates:
        if update.get("citations"):  # 검색한 자료를 근거로 쓴 항목은 말에 없어도 된다
            kept.append(update)
            continue
        fields = {}
        for key, value in (update.get("fields") or {}).items():
            value = str(value or "").strip()
            if not value:
                continue
            grams = bigrams(value)
            numbers = set(re.findall(r"\d+", value))
            if not grams or len(grams & spoken) >= max(1, round(len(grams) * 0.25)) or (numbers and numbers & set(re.findall(r"\d+", text))):
                fields[key] = value
        if fields:
            kept.append({**update, "fields": fields})
    return kept


# 인계자가 모른다·건너뛴다·해당 없다고 분명히 말한 표현(이때만 질문을 건너뛴다)
EXPLICIT_SKIP_RE = re.compile(
    r"모르겠|몰라|모름|모릅니다|모르는|잘 모르|기억(이)? ?안|기억 나지|생각(이)? ?안 ?나|알 수 없|확인(이)? ?어렵|"
    r"건너\s*(뛰|뛸|뜀|뛴)|넘어가|넘겨|다음 질문|스킵|skip|패스|pass|"
    r"해당\s*(사항)?\s*없|없어요|없습니다|없음|없네요|없다|답하기 어렵|답변하기 어렵|나중에",
    re.I,
)


def is_explicit_skip(text: str) -> bool:
    """인계자가 모른다·건너뛴다·해당 없다고 분명히 말했는지."""
    return bool(EXPLICIT_SKIP_RE.search(text or ""))
