"use strict";

const chatForm = document.querySelector("#chat-form");
const messageInput = document.querySelector("#message");
const messages = document.querySelector("#messages");
const chatGuide = document.querySelector("#chat-guide");
const sendButton = document.querySelector("#send");
const newChatButton = document.querySelector("#new-chat");
const processing = document.querySelector("#processing");
const keyWarning = document.querySelector("#key-warning");
const tracePanel = document.querySelector("#trace");
const candidatesPanel = document.querySelector("#plan-candidates");
const candidateList = document.querySelector("#candidate-list");
const sessionStorageKey = "movie-planner-session";
const EXCLUSION_DISPLAY_LIMIT = 10;
const THEATER_DISPLAY_LIMIT = 5;
const EVIDENCE_DISPLAY_LIMIT = 10; // 제공자별 조회 근거 행 표시 상한
let sessionId = null;

try {
  sessionId = sessionStorage.getItem(sessionStorageKey);
} catch {
  // 저장소가 제한된 환경에서는 현재 페이지에서 세션을 유지한다.
}

function saveSession(id) {
  sessionId = id;
  try {
    if (id === null) sessionStorage.removeItem(sessionStorageKey);
    else sessionStorage.setItem(sessionStorageKey, id);
  } catch {
    // 현재 페이지의 대화는 계속할 수 있다.
  }
}

function showMissingKeys(keys) {
  const message = keys.length
    ? `API 키가 설정되지 않았습니다: ${keys.join(", ")} — backend/.env에 키를 넣고 서버를 다시 시작하세요.`
    : "";
  showNotice(keyWarning, message, "warning");
}

function addMessage(speaker, answer, kind = "", sections = []) {
  const bubble = element("div", `bubble ${kind}`);
  bubble.append(element("strong", "speaker", speaker));
  if (sections.length) {
    bubble.classList.add("sourced-answer");
    const sources = element("div", "answer-sources");
    sources.append(...sections);
    bubble.append(sources);
  }
  if (answer) bubble.append(element("p", "answer-text", answer));
  messages.append(bubble);
  chatGuide.hidden = true;
  messages.scrollTop = sections.length ? bubble.offsetTop : messages.scrollHeight;
}

const SOURCE_LABELS = new Map([
  ["KOBIS", "영화진흥위원회 영화관입장권통합전산망 · 통합전산망 오픈 API"],
  ["TMDB", "TMDB"],
  ["unknown", "출처 미확인"],
  ["app", "앱 계산"],
  ["KAKAO", "카카오 로컬"],
  ["notice", "조회 안내"],
]);
const EVIDENCE_TOOLS = new Map([
  ["get_boxoffice", { label: "박스오피스 조회", provider: "KOBIS", candidatePool: true }],
  ["get_now_playing", { label: "현재 상영작 조회", provider: "TMDB", candidatePool: true }],
  ["get_movie_detail", { label: "영화 상세 조회" }],
  ["search_theaters", { label: "영화관 위치 조회", provider: "KAKAO" }],
]);

function declaredProvider({ provider, source }) {
  for (const label of [provider, source]) {
    if (typeof label !== "string") continue;
    if (/\bKOBIS\b|영화진흥위원회|영화관입장권통합전산망/i.test(label)) return "KOBIS";
    if (/\bTMDB\b|The Movie Database/i.test(label)) return "TMDB";
    if (/\bKAKAO\b|카카오/i.test(label)) return "KAKAO";
  }
  return null;
}

function movieProvider(movie) {
  return declaredProvider(movie) || (movie.movie_cd ? "KOBIS" : movie.tmdb_id ? "TMDB" : null);
}

function exclusionProviders(trace, count) {
  const providers = Array(count).fill(null);
  let pool = null;
  let previousCount = 0;
  // 판단 단계는 후보 풀을 한 번씩 처리하며, 제외 후보 수를 누적해서 보낸다.
  for (const { type, stage, payload = {} } of trace) {
    if (type === "stage_start" && stage === "execute") pool = null;
    if (type === "tool_result" && payload.ok === true && EVIDENCE_TOOLS.get(payload.tool)?.candidatePool) {
      pool = declaredProvider(payload);
    }
    if (type === "stage_end" && stage === "judge") {
      const excludedCount = payload.excluded_count;
      if (Number.isInteger(excludedCount) && excludedCount >= previousCount && excludedCount <= count) {
        providers.fill(pool, previousCount, excludedCount);
        previousCount = excludedCount;
      }
      pool = null;
    }
  }
  return providers;
}

function evidenceProvider(entry) {
  return declaredProvider(entry) || EVIDENCE_TOOLS.get(entry.tool)?.provider ||
    movieProvider(entry.args || {}) || "unknown";
}

function answerSection(provider, content) {
  const section = element("section", "answer-source");
  section.append(element("h3", "", SOURCE_LABELS.get(provider)), content);
  return section;
}

function renderAnswerSources(result, isPlan = false) {
  const sources = new Map([...SOURCE_LABELS.keys()].map((provider) => [provider, []]));
  const candidatePools = new Set();
  const evidenceRows = [];
  const notices = sources.get("notice");

  // 실시간 답변의 조회 근거에는 사용자에게 보여 줄 조회 결과 문장이 포함된다.
  for (const entry of result.evidence) {
    const tool = EVIDENCE_TOOLS.get(entry.tool);
    if (!tool) continue;
    const provider = evidenceProvider(entry);
    if (entry.ok === true) {
      evidenceRows.push([provider, `${tool.label} · ${entry.summary || "조회 성공"}`]);
      if (tool.candidatePool) candidatePools.add(provider);
    } else {
      const guidance = entry.tool === "search_theaters" && entry.code === "NO_RESULT"
        ? " 지역명을 다시 알려 주시면 찾아 드릴게요."
        : "";
      notices.push(`${SOURCE_LABELS.get(provider)} · ${tool.label} · ${formatToolFailure(entry.code)}${guidance}`);
    }
  }

  result.recommendations.forEach((movie, index) => {
    const reference = `추천 ${index + 1}`;
    const details = [reference, movie.movie_nm || "제목 미상"];
    if (movie.runtime_min != null) details.push(`러닝타임 ${movie.runtime_min}분`);
    if (movie.watch_grade) details.push(movie.watch_grade);
    if (movie.genres?.length) details.push(movie.genres.join(", "));
    sources.get(movieProvider(movie) || "unknown").push(details.join(" · "));

    const calculation = [];
    if (movie.required_min != null) calculation.push(`필요 시간 ${movie.required_min}분`);
    if (movie.latest_start) calculation.push(`늦어도 ${movie.latest_start}에 시작`);
    if (calculation.length) sources.get("app").push([reference, ...calculation].join(" · "));
  });

  const poolsByExclusion = exclusionProviders(result.trace, result.excluded.length);
  const solePool = candidatePools.size === 1 ? candidatePools.values().next().value : "unknown";
  result.excluded.slice(0, EXCLUSION_DISPLAY_LIMIT).forEach((movie, index) => {
    const provider = movieProvider(movie) || poolsByExclusion[index] || solePool;
    const reference = `제외 ${index + 1}`;
    sources.get(provider).push(`${reference} · ${movie.movie_nm || "제목 미상"}`);
    if (movie.reason) sources.get("app").push(`${reference} · ${movie.reason}`);
  });
  if (result.excluded.length > EXCLUSION_DISPLAY_LIMIT) {
    sources.get("app").push(`제외 후보 외 ${result.excluded.length - EXCLUSION_DISPLAY_LIMIT}건`);
  }

  if (isPlan) {
    const { ad_minutes, formula, start_after, end_by } = result.criteria || {};
    const criterion = [
      ad_minutes != null ? `광고 ${ad_minutes}분 적용` : null,
      formula,
    ].filter(Boolean).join(" · ");
    const timeRange = [
      start_after && `${start_after} 이후 시작`,
      end_by && `${end_by}까지 종료`,
    ].filter(Boolean).join(" · ");
    if (criterion) sources.get("app").push(`판단 기준 · ${criterion}`);
    if (timeRange) sources.get("app").push(`요청 조건 · ${timeRange}`);
    sources.get("app").push(`재계획 ${result.replan_count ?? 0}회`);
  }

  const theaters = result.theaters?.theaters || [];
  for (const theater of theaters.slice(0, THEATER_DISPLAY_LIMIT)) {
    sources.get("KAKAO").push([theater.name, theater.road_address || theater.address, theater.url].filter(Boolean).join(" · "));
  }
  if (theaters.length) {
    notices.push("영화관 검색은 상영 영화를 알려 주지 않으므로 상영 여부는 영화관에서 확인해 주세요.");
  }
  const evidenceShown = new Map();
  for (const [provider, evidence] of evidenceRows) {
    const shown = evidenceShown.get(provider) || 0;
    if (shown < EVIDENCE_DISPLAY_LIMIT) sources.get(provider).push(evidence);
    evidenceShown.set(provider, shown + 1);
  }
  for (const [provider, total] of evidenceShown) {
    if (total > EVIDENCE_DISPLAY_LIMIT) {
      sources.get(provider).push(`조회 근거 외 ${total - EVIDENCE_DISPLAY_LIMIT}건`);
    }
  }
  if (result.estimated_note) notices.push(`${SOURCE_LABELS.get("KOBIS")} · 추정 · ${result.estimated_note}`);

  const sections = [];
  for (const [provider, rows] of sources) {
    if (!rows.length) continue;
    const list = element("ul");
    list.append(...rows.map((row) => element("li", "", row)));
    sections.push(answerSection(provider, list));
  }
  return sections;
}

function limitMessage({ conditions, criteria, limit_reason: reason }) {
  const summary = "후보를 다시 검색했지만 조건에 맞는 영화가 없습니다.";
  const timeQuestion = criteria?.start_after && criteria?.end_by
    ? "끝나는 시각을 늦추거나 시작 시각을 앞당길 수 있을까요?"
    : "관람 시간대를 조정할 수 있을까요?";
  const genreQuestion = "선호 장르를 바꾸거나 빼도 될까요?";
  switch (reason) {
    case "title_not_found": {
      const title = conditions?.movie_title;
      const target = title ? `'${title}'을(를)` : "요청하신 영화를";
      return `후보를 다시 검색했지만 ${target} 찾지 못했습니다. 영화 제목을 확인하거나 다른 영화를 말씀해 주세요.`;
    }
    case "time":
      return `${summary} ${timeQuestion}`;
    case "genre":
      return `${summary} ${genreQuestion}`;
    case "mixed":
      return `${summary} ${timeQuestion} ${genreQuestion}`;
    default:
      return `${summary} 관람 조건을 조정할 수 있을까요?`;
  }
}

function addAgentMessage(result) {
  const labels = { config_error: "키 설정 안내", clarify: "추가 확인", limit: "재계획 상한 도달" };
  const { status, answer, recommendations, excluded, evidence, theaters } = result;
  const speaker = labels[status] || "Agent";
  const hasSourceData = Boolean(recommendations.length || excluded.length || evidence.length || theaters);
  if (status === "limit" || (status === "ok" && hasSourceData)) {
    const message = status === "limit" ? limitMessage(result) : null;
    addMessage(speaker, message, status, renderAnswerSources(result, true));
    return;
  }

  if (status === "fact" && hasSourceData) {
    const providers = new Set(evidence.map(evidenceProvider));
    if (theaters) providers.add("KAKAO");
    if (providers.size === 1 && providers.has("KOBIS")) {
      addMessage(speaker, null, status, [
        answerSection("KOBIS", element("p", "answer-text", answer)),
      ]);
    } else {
      addMessage(speaker, null, status, renderAnswerSources(result));
    }
    return;
  }

  // 재계획 중 키 오류가 나면 첫 줄의 설정 안내 뒤에 앞선 제외 후보와 사유가 이어진다.
  if (status === "config_error" && excluded.length) {
    addMessage(speaker, answer.split("\n", 1)[0], status, renderAnswerSources(result));
    return;
  }

  addMessage(speaker, answer, status);
}

function candidateField(container, label, name, type, value = "") {
  const wrapper = element("label", "", label);
  const input = element("input");
  input.name = name;
  input.type = type;
  input.value = value ?? "";
  input.required = true;
  wrapper.append(input);
  container.append(wrapper);
}

function renderCandidates(result) {
  const theaters = result.theaters?.theaters || [];
  const cards = result.plan_candidates.map((candidate, index) => {
    const card = element("article", "candidate");
    card.append(element("h3", "", `추천 ${index + 1} 계획`));

    const form = element("form");
    candidateField(form, "영화명", "movie_nm", "text", candidate.movie_nm);
    form.elements.namedItem("movie_nm").placeholder = "볼 영화 제목을 입력하세요";
    const fields = element("div", "fields");
    candidateField(fields, "관람 날짜", "watch_date", "date", candidate.watch_date);
    candidateField(fields, "시작 시각", "start_time", "time", candidate.start_time);
    candidateField(fields, "종료 시각", "end_time", "time");
    form.append(fields);

    const select = element("select");
    if (theaters.length) {
      const label = element("label", "", "영화관");
      const noTheater = element("option", "", "선택 안 함");
      noTheater.value = "";
      select.append(noTheater);
      theaters.forEach((theater, theaterIndex) => {
        const address = theater.road_address || theater.address;
        const option = element("option", "", address ? `${theater.name} · ${address}` : theater.name);
        option.value = String(theaterIndex);
        select.append(option);
      });
      label.append(select);
      form.append(label);
    }

    const button = element("button", "", "계획 저장");
    const notice = element("p", "notice");
    notice.setAttribute("role", "status");
    notice.hidden = true;
    form.append(button, notice);
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (button.disabled) return;
      const theater = select.value === "" ? null : theaters[Number(select.value)];
      const fields = new FormData(form);
      const body = {
        movie_nm: fields.get("movie_nm").trim(),
        watch_date: fields.get("watch_date"),
        start_time: fields.get("start_time"),
        end_time: fields.get("end_time"),
        theater_name: theater?.name ?? null,
        theater_address: theater?.road_address || theater?.address || null,
        memo: null,
      };
      if (candidate.tmdb_id != null) body.tmdb_id = candidate.tmdb_id;
      if (!body.movie_nm) {
        showNotice(notice, "볼 영화 제목을 입력하세요", "error");
        form.elements.namedItem("movie_nm").focus();
        return;
      }
      button.disabled = true;
      showNotice(notice);
      try {
        const saved = await apiRequest("/api/plans", { method: "POST", body });
        showNotice(notice, saved.warning || "관람 계획을 저장했습니다.", saved.warning ? "warning" : "");
      } catch (error) {
        showNotice(notice, displayError(error), "error");
        button.disabled = false;
      }
    });
    card.append(form);
    return card;
  });
  candidateList.replaceChildren(...cards);
  candidatesPanel.hidden = cards.length === 0;
}

messageInput.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey || event.isComposing || event.keyCode === 229) return;
  event.preventDefault();
  if (!sendButton.disabled) chatForm.requestSubmit();
});

chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = messageInput.value.trim();
  if (!message || sendButton.disabled) {
    messageInput.focus();
    return;
  }
  addMessage("나", message, "user");
  messageInput.value = "";
  sendButton.disabled = true;
  newChatButton.disabled = true;
  processing.hidden = false;

  try {
    const result = await apiRequest("/api/chat", {
      method: "POST", body: { session_id: sessionId, message },
    });
    saveSession(result.session_id);
    showMissingKeys(result.missing_keys);
    addAgentMessage(result);
    renderTrace(tracePanel, result.trace);
    renderCandidates(result);
  } catch (error) {
    addMessage("Agent", displayError(error), "error");
  } finally {
    sendButton.disabled = false;
    newChatButton.disabled = false;
    processing.hidden = true;
    messageInput.focus();
  }
});

newChatButton.addEventListener("click", () => {
  saveSession(null);
  messages.replaceChildren();
  candidateList.replaceChildren();
  chatGuide.hidden = false;
  candidatesPanel.hidden = true;
  tracePanel.replaceChildren(element("p", "muted", "메시지를 전송하면 실행 과정이 표시됩니다."));
  messageInput.value = "";
  messageInput.focus();
});

apiRequest("/api/chat/config")
  .then((config) => showMissingKeys(config.missing_keys))
  .catch((error) => showNotice(keyWarning, displayError(error), "error"));
