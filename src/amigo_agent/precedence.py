"""정보 충돌 우선순위.

같은 항목의 같은 필드에 서로 다른 값이 들어오면, 아래 순위(숫자가 작을수록 강함)로 어느 값을 쓸지 정한다.
충돌은 자동으로 우선순위대로 처리하고, 처리 내역은 문서의 '확인 필요 사항'에 남겨 인계자가 최종 검토 때 확인한다.

    1. 인계자가 확인한 최신 답변            (번복이든 문서와의 충돌이든 항상 가장 우선)
    2. 작성일이 더 최근인 공식 문서
    3. 회의록·메일
    4. 날짜가 없는 참고자료

- 같은 순위끼리 충돌하면 시각(분 단위)이 더 최근인 쪽을 쓴다. 날짜가 있는 쪽이 없는 쪽보다 강하다.
- 인계자 답변끼리는 초 단위로 비교하며, 같은 시각이면 나중에 들어온 답변을 쓴다.
- 같은 순위·같은 시각(또는 둘 다 날짜 없음)인 문서끼리는 먼저 확인된 값을 유지한다.
- '공식 문서'는 회의록·메일이 아니면서 작성일(created_at, 없으면 modified_at)이 있는 파일이다.
  자료 메타데이터에 `doc_class`(official / minutes / mail / reference)가 있으면 그 값을 따른다.
"""

from __future__ import annotations

import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

RANK_USER = 1
RANK_OFFICIAL = 2
RANK_MINUTES_MAIL = 3
RANK_REFERENCE = 4

RANK_LABELS = {
    RANK_USER: "인계자 답변",
    RANK_OFFICIAL: "공식 문서",
    RANK_MINUTES_MAIL: "회의록·메일",
    RANK_REFERENCE: "날짜 없는 참고자료",
}

_MAIL_TYPES = {"eml", "msg", "mail", "email"}
_MINUTES_RE = re.compile(r"회의록|회의\s*결과|미팅|minutes", re.I)
_DOC_CLASS_RANK = {"official": RANK_OFFICIAL, "minutes": RANK_MINUTES_MAIL, "mail": RANK_MINUTES_MAIL, "reference": RANK_REFERENCE}
_DATE_RE = re.compile(r"^(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})(?:[ T]+(\d{1,2}):(\d{2})(?::(\d{2}))?)?")


def now_ts() -> str:
    """인계자 답변 시각(초 단위). 서버 로컬 시각 기준 naive 로 통일한다."""
    return datetime.now().replace(microsecond=0).isoformat(timespec="seconds")


def parse_ts(value: Any) -> str | None:
    """여러 날짜 표기를 'YYYY-MM-DDTHH:MM' 문자열로 정규화한다(분 단위). 읽을 수 없으면 None."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip()
        if not text:
            return None
        dt = None
        match = _DATE_RE.match(text)
        if match and "T" not in text and "+" not in text[10:] and not text.endswith("Z"):
            y, mo, d, h, mi, _s = match.groups()
            try:
                dt = datetime(int(y), int(mo), int(d), int(h or 0), int(mi or 0))
            except ValueError:
                dt = None
        if dt is None:
            try:
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                try:
                    dt = parsedate_to_datetime(text)  # 메일 헤더(RFC 2822)
                except (TypeError, ValueError):
                    return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)  # 시간대가 있는 값은 서버 로컬 시각으로 맞춘다
    return dt.isoformat(timespec="minutes")


def classify(meta: dict[str, Any] | None) -> dict[str, Any]:
    """자료 메타데이터로 순위(rank)·시각(ts)·분류(doc_class)를 정한다."""
    meta = meta or {}
    source_type = str(meta.get("source_type") or meta.get("file_type") or "").lower().lstrip(".")
    name = str(meta.get("file_name") or meta.get("source_name") or "")
    declared = str(meta.get("doc_class") or "").lower()
    created = parse_ts(meta.get("created_at")) or parse_ts(meta.get("modified_at"))
    sent = parse_ts(meta.get("sent_at"))

    if source_type == "chat":
        return {"rank": RANK_USER, "ts": created, "doc_class": "user"}
    if declared in _DOC_CLASS_RANK:
        doc_class = declared
    elif source_type in _MAIL_TYPES or sent:
        doc_class = "mail"
    elif _MINUTES_RE.search(name):
        doc_class = "minutes"
    elif created:
        doc_class = "official"
    else:
        doc_class = "reference"

    rank = _DOC_CLASS_RANK[doc_class]
    ts = sent or created if doc_class == "mail" else created or sent
    if doc_class == "official" and not ts:
        rank, doc_class = RANK_REFERENCE, "reference"  # 날짜 없는 공식 표기 문서는 참고자료로 본다
    return {"rank": rank, "ts": ts, "doc_class": doc_class}


def user_meta(label: str = "") -> dict[str, Any]:
    return {"rank": RANK_USER, "ts": now_ts(), "doc_class": "user", "label": label}


def beats(new: dict[str, Any], old: dict[str, Any] | None) -> bool:
    """새 값(new)이 기존 값(old)을 이기는지."""
    if old is None:
        return True
    if new["rank"] != old["rank"]:
        return new["rank"] < old["rank"]
    n, o = new.get("ts"), old.get("ts")
    if n and o:
        if n != o:
            return n > o
        return new["rank"] == RANK_USER  # 같은 시각이면 인계자의 나중 답변이 이긴다
    if n and not o:
        return True
    if o and not n:
        return False
    return new["rank"] == RANK_USER


def reason(winner: dict[str, Any], loser: dict[str, Any]) -> str:
    if winner["rank"] == RANK_USER:
        return "인계자의 최신 답변 우선"
    if winner["rank"] != loser["rank"]:
        return f"{RANK_LABELS[winner['rank']]} 우선({RANK_LABELS[loser['rank']]}보다 높은 순위)"
    if winner.get("ts") and winner.get("ts") != loser.get("ts"):
        return f"같은 순위({RANK_LABELS[winner['rank']]}) 중 더 최근 자료 우선"
    return "같은 순위·같은 시각이라 먼저 확인된 값 유지"
