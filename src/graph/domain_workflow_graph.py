"""Inner, mode-aware domain graph for FIN-C2-177."""

from __future__ import annotations

from langgraph.graph import END, START

from framework.errors import ConfigError
from framework.graph.base_graph import BaseGraph
from framework.schemas.agent_status import AgentStatus
from src.nodes.workflow_nodes import ComparisonNode, CurrentAssessmentNode, DeterministicCheckNode, EvidenceVerifyNode, FeedbackNode, IngestNode, InputDecodeNode, RecallNode
from src.schemas.state import State


class OutsourcingBriefWorkflowGraph(BaseGraph):
    """Three-mode Cat-2 workflow with a linear invoke assessment pipeline."""

    @property
    def name(self) -> str:
        return "outsourcing_due_diligence_workflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        threshold = self.config.get("evidence_confidence_threshold", 0.8)
        if not isinstance(threshold, (int, float)) or not 0 <= threshold <= 1:
            raise ConfigError("evidence_confidence_threshold must be between 0 and 1")

    def register_nodes(self) -> None:
        self._nodes = {
            "decode": InputDecodeNode(self.config), "ingest": IngestNode(self.config), "feedback": FeedbackNode(self.config),
            "stage1": DeterministicCheckNode(self.config), "stage2": RecallNode(self.config),
            "stage3a": CurrentAssessmentNode(self.config), "stage3b": ComparisonNode(self.config),
            "stage4": EvidenceVerifyNode(self.config),
        }

    def add_edges(self) -> None:
        self._sg.add_edge(START, "decode")
        self._sg.add_conditional_edges("decode", self.route)
        self._sg.add_edge("ingest", END)
        self._sg.add_edge("feedback", END)
        self._sg.add_conditional_edges("stage1", self._next_after_stage1)
        self._sg.add_conditional_edges("stage2", self._next_after_stage2)
        self._sg.add_conditional_edges("stage3a", self._next_after_stage3a)
        self._sg.add_conditional_edges("stage3b", self._next_after_stage3b)
        self._sg.add_edge("stage4", END)

    def route(self, state: State) -> str:
        if state.get("status") == AgentStatus.ERROR.value:
            return END
        return {"ingest": "ingest", "feedback": "feedback", "invoke": "stage1"}.get(state.get("mode", ""), END)

    @staticmethod
    def _next(state: State, destination: str) -> str:
        return END if state.get("status") == AgentStatus.ERROR.value else destination

    def _next_after_stage1(self, state: State) -> str:
        return self._next(state, "stage2")

    def _next_after_stage2(self, state: State) -> str:
        return self._next(state, "stage3a")

    def _next_after_stage3a(self, state: State) -> str:
        return self._next(state, "stage3b")

    def _next_after_stage3b(self, state: State) -> str:
        return self._next(state, "stage4")

    def get_output(self, state: State) -> dict:
        mode = state.get("mode")
        output = {"status": state.get("status"), "mode": mode, "trace_id": state.get("trace_id", ""), "correlation_id": state.get("correlation_id", ""), "error_log": state.get("error_log", [])}
        if mode == "invoke":
            output.update({key: state.get(key) for key in ("evidence_brief", "evidence_map", "current_evidence_basis", "changed_since_last", "requires_human_review", "degradation_reason", "assessment_ref", "source_registry")})
        elif mode == "ingest":
            output["ingest_result"] = state.get("ingest_result")
        elif mode == "feedback":
            output["feedback_result"] = state.get("feedback_result")
        return output
