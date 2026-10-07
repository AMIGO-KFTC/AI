"""STAGE 1 → 4 상태 머신(LangGraph StateGraph) 구성.

    START → prepare ─Send×6→ analyze_slot → collect_gaps → summarize → choose_next ─┬→ ask → wait_user ⟲
                                                                                    └→ compose → wait_user
    wait_user ─┬ message → interpret ─┬→ choose_next (확인·건너뛰기 후 다음 질문)
               │                     ├→ wait_user   (교차 확인 대기·되묻기)
               │                     └→ compose     (문서 생성 요청·검토 의견 반영)
               ├ skip    → skip_gap → choose_next
               ├ files   → reanalyze → choose_next / wait_user / compose
               └ finish  → request_finish → compose

`python -m amigo_agent graph` 로 Mermaid 다이어그램을 출력할 수 있다.
"""

from __future__ import annotations

from langgraph.graph import START, StateGraph

from . import nodes
from .nodes import AgentContext
from .state import AgentState


def build_graph() -> StateGraph:
    graph = StateGraph(AgentState, context_schema=AgentContext)

    graph.add_node("prepare", nodes.prepare)
    graph.add_node("analyze_slot", nodes.analyze_slot)
    graph.add_node("collect_gaps", nodes.collect_gaps)
    graph.add_node("summarize", nodes.summarize)
    graph.add_node("choose_next", nodes.choose_next)
    graph.add_node("ask", nodes.ask)
    graph.add_node("wait_user", nodes.wait_user)
    graph.add_node("interpret", nodes.interpret)
    graph.add_node("skip_gap", nodes.skip_gap)
    graph.add_node("request_finish", nodes.request_finish)
    graph.add_node("reanalyze", nodes.reanalyze)
    graph.add_node("compose", nodes.compose)

    # STAGE 1 → 2
    graph.add_edge(START, "prepare")
    graph.add_conditional_edges("prepare", nodes.fan_out, ["analyze_slot"])
    graph.add_edge("analyze_slot", "collect_gaps")
    graph.add_edge("collect_gaps", "summarize")
    graph.add_edge("summarize", "choose_next")

    # STAGE 3 루프
    graph.add_conditional_edges("choose_next", nodes.route_next, ["ask", "compose"])
    graph.add_edge("ask", "wait_user")
    graph.add_conditional_edges("wait_user", nodes.route_event, ["interpret", "skip_gap", "reanalyze", "request_finish"])
    graph.add_conditional_edges("interpret", nodes.route_after_interpret, ["choose_next", "wait_user", "compose"])
    graph.add_conditional_edges("skip_gap", nodes.route_after_skip, ["choose_next", "wait_user"])
    graph.add_conditional_edges("reanalyze", nodes.route_after_reanalyze, ["choose_next", "wait_user", "compose"])
    graph.add_edge("request_finish", "compose")

    # STAGE 4 → 검토 의견 대기
    graph.add_edge("compose", "wait_user")
    return graph
