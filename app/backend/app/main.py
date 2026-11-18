import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api import chat, logs, plans
from .events.bus import bus
from .logging_agent.agent import LoggingAgent
from .store.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    agent = None
    if os.getenv("DISABLE_LOGGING_AGENT") != "1":
        agent = LoggingAgent()
        agent.start(bus)
    try:
        yield
    finally:
        if agent is not None:
            agent.stop()


# FastAPI 0.141의 include_router는 path가 없는 라우터 래퍼를 app.routes에 넣는다.
# TASK PACKET의 경로 점검 명령이 각 경로를 읽을 수 있도록 라우터의 경로를 등록한다.
app = FastAPI(
    title="Movie Planner API",
    lifespan=lifespan,
    routes=[*chat.router.routes, *plans.router.routes, *logs.router.routes],
)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "입력값을 확인해 주세요."
    if request.url.path.rstrip("/") == "/api/chat":
        if any(error["loc"] == ("body", "session_id") for error in exc.errors()):
            detail = "세션 ID는 영문자, 숫자, 밑줄, 하이픈을 사용해 1자 이상 64자 이하여야 합니다."
        else:
            detail = "메시지는 공백만으로 구성할 수 없으며 1자 이상 1000자 이하여야 합니다."
    return JSONResponse(status_code=422, content={"detail": detail})


@app.exception_handler(Exception)
async def server_error(request: Request, _: Exception) -> JSONResponse:
    logging.getLogger(__name__).exception("unhandled error: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "서버 오류가 발생했습니다."})


@app.middleware("http")
async def handle_unexpected_error(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return await server_error(request, exc)


# 나중에 등록한 CORS가 바깥에서 500 응답에도 헤더를 추가한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5500", "http://127.0.0.1:5500"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
