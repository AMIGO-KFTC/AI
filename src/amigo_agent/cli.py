"""터미널에서 에이전트를 체험하는 도구.

    python -m amigo_agent chat --files ../RAG/samples/*.pdf ../RAG/samples/*.eml   # 자료를 적재하고 인터뷰 시작
    python -m amigo_agent chat --kb demo                                         # 이미 적재된 지식베이스 사용
    python -m amigo_agent graph                                                  # 상태 머신 Mermaid 출력

채팅 중 명령: /skip(건너뛰기) /finish(문서 생성) /doc(문서 보기) /state(슬롯 상태) /quit
API 키가 없으면 규칙 기반 오프라인 엔진으로 동작한다(AMIGO_LLM_MODE=offline|claude|auto).
"""

from __future__ import annotations

import argparse
import glob
import sys
import uuid

from .agent import HandoverAgent
from .template import COVERAGE_LABELS, SLOT_BY_KEY


def _print_event(event: dict) -> None:
    kind = event.get("type")
    if kind == "stage":
        print(f"\n━━━ STAGE {event.get('number')} · {event.get('label')} ━━━")
    elif kind == "progress":
        print(f"  … {event.get('message')}")
    elif kind == "message":
        print(f"\n🤖 {event.get('content')}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="amigo-agent")
    sub = parser.add_subparsers(dest="command", required=True)
    chat = sub.add_parser("chat", help="인수인계 인터뷰 실행")
    chat.add_argument("--kb", default=None, help="지식베이스 ID(기본: 새로 생성)")
    chat.add_argument("--files", nargs="*", default=[], help="적재할 파일/링크")
    chat.add_argument("--name", default="김민수")
    chat.add_argument("--org", default="디지털전략부 웹서비스팀")
    chat.add_argument("--position", default="과장")
    chat.add_argument("--duties", default="기관 홈페이지 운영 및 웹 접근성 관리")
    chat.add_argument("--successor", default="이서연 대리")
    sub.add_parser("graph", help="상태 머신 Mermaid 다이어그램 출력")
    args = parser.parse_args(argv)

    agent = HandoverAgent()
    if args.command == "graph":
        print(agent.mermaid())
        return 0

    try:
        from amigo_rag import KnowledgeBase, ParseError
    except ImportError:
        print("amigo_rag 가 필요합니다: pip install -e ../RAG", file=sys.stderr)
        return 1

    kb = KnowledgeBase(args.kb or f"cli-{uuid.uuid4().hex[:8]}")
    files: list[str] = []
    for item in args.files:  # Windows PowerShell 은 *.pdf 를 펼쳐 주지 않으므로 직접 확장
        is_pattern = not item.startswith(("http://", "https://")) and any(ch in item for ch in "*?[")
        matches = sorted(glob.glob(item)) if is_pattern else []
        files.extend(matches or [item])
    for item in files:
        try:
            res = kb.add_url(item) if item.startswith(("http://", "https://")) else kb.add_file(item)
            print(f"✔ {res.source_name} ({res.chunk_count}개 청크)")
        except ParseError as exc:
            print(f"✘ {item}: {exc}")

    print(f"엔진: {agent.describe()}")
    profile = {
        "name": args.name,
        "organization": args.org,
        "position": args.position,
        "duties": args.duties,
        "successor": args.successor,
    }
    thread = kb.kb_id
    agent.start(thread, profile, kb, on_event=_print_event)
    while True:
        try:
            text = input("👤 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not text:
            continue
        if text == "/quit":
            return 0
        if text == "/doc":
            print(agent.snapshot(thread)["document"] or "(아직 문서가 없습니다)")
            continue
        if text == "/state":
            for key, slot in agent.snapshot(thread)["slots"].items():
                print(f"- {SLOT_BY_KEY[key].title}: {COVERAGE_LABELS.get(slot.get('coverage'), '')} ({len(slot.get('items', []))}건)")
            continue
        if text == "/skip":
            agent.skip(thread, kb, on_event=_print_event)
        elif text == "/finish":
            agent.finish(thread, kb, on_event=_print_event)
        else:
            agent.send_message(thread, text, kb, on_event=_print_event)


if __name__ == "__main__":
    raise SystemExit(main())
