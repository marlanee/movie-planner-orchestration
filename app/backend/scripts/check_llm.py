"""GMS 응답과 영화 플래너 Tool 호출 형식을 확인한다."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import GMS_BASE_URL, GMS_MODEL, get_key
from app.tools.registry import to_openai_tools


def main() -> int:
    key = get_key("GMS_KEY")
    if key is None:
        print("GMS_KEY 없음 — .env 설정 필요")
        return 0

    try:
        from langchain_openai import ChatOpenAI

        llm = ChatOpenAI(
            model=GMS_MODEL, api_key=key, base_url=GMS_BASE_URL, timeout=10, max_retries=0
        )
        reply = llm.invoke("한 문장으로 인사해")
        print(reply.text[:80])

        tool_llm = llm.bind_tools(to_openai_tools())
        reply = tool_llm.invoke("토요일 3시 이후 강남에서 영화 보고 7시 전에 끝내고 싶어")
        for call in reply.tool_calls:
            print(json.dumps({"name": call["name"], "args": call["args"]}, ensure_ascii=False))
        if not reply.tool_calls:
            print("Tool 호출 없음")
    except Exception as error:
        print(type(error).__name__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
