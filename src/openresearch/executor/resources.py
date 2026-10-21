from openresearch.executor.gates import Gatekeeper
from openresearch.executor.models import DAG, ExecutorConfig


def effective_graph(graph: DAG, config: ExecutorConfig, gates: Gatekeeper) -> DAG:
    nodes = []
    for node in graph.nodes:
        if node.io_weight > gates.config.max_concurrent_io:
            raise ValueError("node IO weight exceeds configured capacity")
        if node.gpu_weight and not config.sandbox.gpu_enabled:
            raise ValueError("GPU use requires explicit gpu_enabled permission")
        # 整个 GPU 沙箱中的默认节点也至少占一个 GPU 配额，不能启用设备后绕过门控。
        weight = max(1, node.gpu_weight) if config.sandbox.gpu_enabled else 0
        if weight > gates.config.max_gpu_tasks:
            raise ValueError("node GPU weight exceeds configured capacity")
        nodes.append(node.model_copy(update={"gpu_weight": weight}))
    return DAG(nodes=tuple(nodes))
