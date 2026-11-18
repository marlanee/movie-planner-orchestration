"""실행 없이 에이전트 그래프를 Mermaid로 출력한다."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.graph import build_graph


if __name__ == "__main__":
    print(build_graph(llm=object()).get_graph().draw_mermaid())
