"""Outer fixed AgentBaseGraph for FIN-C2-177."""

from framework.graph.agent_base_graph import AgentBaseGraph
from src.nodes.main_node import OutsourcingBriefGraphNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class OutsourcingDueDiligenceBriefAgent(AgentBaseGraph):
    """Fixed backbone enclosing the cached Cat-2 outsourcing workflow."""

    @property
    def name(self) -> str:
        return "FinancialOutsourcingDueDiligenceEvidenceBriefAgent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = OutsourcingBriefGraphNode(self.config)
        self._nodes["post_process"] = PostProcessNode()


Graph = OutsourcingDueDiligenceBriefAgent
