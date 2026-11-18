"use strict";

const API_BASE = "http://localhost:8000";
const NETWORK_ERROR = "서버에 연결할 수 없습니다. 백엔드(uvicorn)가 켜져 있는지 확인하세요.";
const REQUEST_ERROR = "요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요.";

class ApiError extends Error {}

function displayError(error) {
  return error instanceof ApiError ? error.message : REQUEST_ERROR;
}

async function apiRequest(path, { method = "GET", body } = {}) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }

  let response;
  try {
    response = await fetch(`${API_BASE}${path}`, options);
  } catch {
    throw new ApiError(NETWORK_ERROR);
  }

  if (response.status === 204) return null;
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = data?.detail;
    const displayable = typeof detail === "string" && /[가-힣]/.test(detail) &&
      !/[\r\n]|https?:|traceback|exception|stack trace/i.test(detail);
    throw new ApiError(displayable ? detail : REQUEST_ERROR);
  }
  if (data === null) throw new ApiError(REQUEST_ERROR);
  return data;
}

function element(tag, className = "", value = "") {
  const node = document.createElement(tag);
  node.className = className;
  node.textContent = value;
  return node;
}

function showNotice(node, message = "", kind = "") {
  node.className = kind ? `notice ${kind}` : "notice";
  node.textContent = message;
  node.hidden = message === "";
}

function actionButton(label, kind = "secondary") {
  const button = element("button", kind, label);
  button.type = "button";
  return button;
}

function formatTimestamp(value) {
  return value ? value.slice(0, 19).replace("T", " ") : "—";
}

const STAGES = new Map([
  ["analyze", "요청 분석"], ["plan", "작업 계획"], ["execute", "Tool 실행"],
  ["judge", "제약 판단"], ["replan", "재계획"], ["respond", "응답 생성"],
]);
const TOOL_FAILURES = new Map([
  ["INVALID_KEY", "API 키 누락/오류 — .env 확인"],
  ["NO_RUNTIME", "러닝타임 정보 없음"],
  ["NO_RESULT", "검색 결과 없음"],
  ["NO_DATA", "조회 결과 데이터 없음"],
  ["NOT_FOUND", "대상 정보를 찾지 못함"],
  ["BAD_ARGUMENT", "입력값 오류"],
  ["UPSTREAM_ERROR", "외부 API 응답 없음"],
]);
const INTENTS = new Map([
  ["plan_movie", "관람 계획"], ["fact_query", "정보 조회"], ["smalltalk", "일반 대화"],
]);
const PROVIDER_LABELS = new Map([
  ["KOBIS", "KOBIS"], ["TMDB", "TMDB"], ["KAKAO", "카카오"],
]);
const TRACE_INPUTS = new Map([
  ["target_date", "요청 날짜"], ["period", "조회 단위"], ["page", "페이지"], ["region", "지역"],
]);

function formatToolFailure(code) {
  const message = TOOL_FAILURES.get(code);
  return message ? `${message} (${code})` : "조회 실패";
}

function renderToolTrace(request, result, showSeq) {
  const call = request?.payload || {};
  const outcome = result?.payload || {};
  const tool = call.tool || outcome.tool || "Tool";
  const provider = PROVIDER_LABELS.get(call.provider || outcome.provider) || "제공자 기록 없음";
  const status = outcome.ok === true ? "성공" : outcome.ok === false ? "실패" : "결과 기록 없음";
  const statusClass = outcome.ok === true ? "success" : outcome.ok === false ? "error" : "";
  const duration = outcome.duration_ms ?? result?.duration_ms;
  const item = element("li", outcome.ok === false ? "trace-event failed" : "trace-event");
  const title = element("div", "trace-title");
  title.append(
    element("strong", "", tool),
    element("span", "muted", provider),
    element("span", statusClass ? `badge ${statusClass}` : "muted", status),
    element("span", "muted", Number.isFinite(duration) ? `${duration} ms` : "소요 시간 기록 없음"),
  );
  if (showSeq) {
    const seqs = [request?.seq, result?.seq].filter(Number.isInteger);
    if (seqs.length) title.append(element("small", "muted", `seq ${seqs.join(", ")}`));
  }
  item.append(title);

  const inputs = [];
  for (const [key, label] of TRACE_INPUTS) {
    const value = call.args?.[key];
    if (typeof value === "string" || typeof value === "number") inputs.push(`${label} ${value}`);
  }
  const previousArgs = Array.isArray(call.arg_names)
    ? call.arg_names.filter((name) => name === "movie_cd" || name === "tmdb_id")
    : [];
  if (previousArgs.length) inputs.push(`${previousArgs.join(", ")} 등 앞 단계 결과 사용`);
  item.append(element("p", "muted", `입력 · ${inputs.join(" · ") || "입력 기록 없음"}`));
  if (outcome.ok === false) item.append(element("p", "muted", formatToolFailure(outcome.code)));
  return item;
}

function renderTrace(container, events, { showSeq = false } = {}) {
  if (!events.length) {
    container.replaceChildren(element("p", "muted", "표시할 실행 이벤트가 없습니다."));
    return;
  }

  const list = element("ol", "trace-list");
  const requests = new Set();
  const results = new Map();
  for (const event of events) {
    const id = event.payload?.call_id;
    if (!id) continue;
    if (event.type === "tool_call") requests.add(id);
    if (event.type === "tool_result") results.set(id, event);
  }
  let replanCount = 0;
  for (const event of events) {
    const payload = event.payload || {};
    if (event.type === "tool_call") {
      list.append(renderToolTrace(event, results.get(payload.call_id), showSeq));
      continue;
    }
    if (event.type === "tool_result") {
      if (!requests.has(payload.call_id)) list.append(renderToolTrace(null, event, showSeq));
      continue;
    }
    const stage = STAGES.get(event.stage) || "Agent";
    const item = element("li", "trace-event");
    const title = element("div", "trace-title");
    let label;
    let description;
    let badge;

    switch (event.type) {
      case "stage_start":
        label = `${stage} 시작`;
        break;
      case "stage_end":
        label = `${stage} 종료`;
        break;
      case "llm_decision":
        label = `의도 판단 · ${INTENTS.get(payload.intent) || payload.intent || "미분류"}`;
        description = payload.reason;
        break;
      case "replan":
        replanCount = payload.replan_count ?? replanCount + 1;
        label = payload.next_pool || "후보 다시 검색";
        badge = element("span", "badge warning", `재계획 ${replanCount}회차`);
        description = payload.reason;
        item.classList.add("replan");
        break;
      case "error":
        label = `${stage} 실행 중 오류`;
        badge = element("span", "badge error", "오류");
        item.classList.add("failed");
        break;
      default:
        label = "실행 이벤트";
    }

    if (badge) title.append(badge);
    title.append(element("strong", "", label));
    if (event.duration_ms != null) {
      title.append(element("span", "muted", `${event.duration_ms} ms`));
    }
    if (showSeq && Number.isInteger(event.seq)) {
      title.append(element("small", "muted", `seq ${event.seq}`));
    }
    item.append(title);
    if (description) item.append(element("p", "muted", description));
    list.append(item);
  }
  container.replaceChildren(list);
}
