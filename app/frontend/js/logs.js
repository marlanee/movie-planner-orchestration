"use strict";

const logList = document.querySelector("#log-list");
const logStatus = document.querySelector("#log-status");
const logSession = document.querySelector("#log-session");
const logDetail = document.querySelector("#log-detail");
let selectedLogId = null;
let logDetailVersion = 0;
let logListVersion = 0;

const anomalyRules = new Map([
  ["repeated_call", "같은 입력 반복 호출"],
  ["excessive_replan", "재계획 상한"],
  ["unplanned_call", "계획에 없던 호출"],
]);

function updateSelectedLog() {
  for (const button of logList.querySelectorAll(".record-open")) {
    button.setAttribute("aria-pressed", String(button.dataset.sessionId === selectedLogId));
  }
}

function renderAnomalyReports(reports) {
  const section = element("section", "anomaly-reports");
  section.append(element("h3", "", "실행별 이상 판정"));
  if (!reports.length) {
    section.append(element("p", "muted", "저장된 판정이 없습니다."));
    return section;
  }

  const list = element("ol", "trace-list");
  for (const report of reports) {
    const item = element("li", "trace-event");
    const title = element("div", "trace-title");
    title.append(element("strong", "", `실행 ${report.run_index}`));
    if (report.verdict === "anomaly") {
      title.append(element("span", "badge error", "이상"));
    } else if (report.verdict === "undetermined") {
      title.append(element("span", "badge neutral", "판정 불가"));
    } else {
      title.append(element("span", "badge success", "정상"));
    }
    if (report.warning_count) {
      title.append(element("span", "badge warning", `경고 ${report.warning_count}`));
    }
    item.append(title);
    if (report.findings.length || report.missing_evidence.length) {
      const evidence = element("ul", "anomaly-evidence");
      for (const finding of report.findings) {
        const rule = anomalyRules.get(finding.rule) || finding.rule;
        const seqs = finding.evidence_seqs.join(", ") || "없음";
        evidence.append(element("li", "", `${rule} · 근거 seq ${seqs} · ${finding.detail}`));
      }
      for (const reason of report.missing_evidence) {
        evidence.append(element("li", "muted", reason));
      }
      item.append(evidence);
    }
    if (report.diagnosis?.status === "ok" || report.diagnosis?.status === "failed") {
      const diagnosis = element("div", "anomaly-diagnosis");
      diagnosis.append(
        element("strong", "", "LLM 진단"),
        element("p", "", report.diagnosis.status === "ok" ? report.diagnosis.text : "진단 실패"),
      );
      item.append(diagnosis);
    } else if (report.verdict === "anomaly" && report.diagnosis == null) {
      item.append(element("p", "muted", "진단 대기 또는 없음"));
    }
    list.append(item);
  }
  section.append(list);
  return section;
}

async function openLog(sessionId) {
  const version = ++logDetailVersion;
  selectedLogId = sessionId;
  updateSelectedLog();
  logSession.textContent = sessionId;
  logDetail.replaceChildren(element("p", "muted", "이벤트를 불러오는 중…"));
  const path = `/api/logs/${encodeURIComponent(sessionId)}`;
  const [detail, anomalies] = await Promise.allSettled([
    apiRequest(path),
    apiRequest(`${path}/anomalies`),
  ]);
  if (version !== logDetailVersion) return;
  if (detail.status === "rejected") {
    logDetail.replaceChildren(element("p", "notice error", displayError(detail.reason)));
    return;
  }
  renderTrace(logDetail, detail.value.events, { showSeq: true });
  const reports = anomalies.status === "fulfilled"
    ? renderAnomalyReports(anomalies.value)
    : element("p", "notice error", "실행별 판정을 불러오지 못했습니다.");
  logDetail.prepend(reports);
}

async function removeLog(sessionId, button) {
  if (!window.confirm("이 대화 세션의 실행 로그를 삭제할까요?")) return;
  button.disabled = true;
  try {
    await apiRequest(`/api/logs/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    if (selectedLogId === sessionId) {
      selectedLogId = null;
      logDetailVersion++;
      logSession.textContent = "";
      logDetail.replaceChildren(element("p", "muted", "대화 세션을 선택하면 실행 과정이 표시됩니다."));
    }
    showNotice(logStatus, "실행 로그를 삭제했습니다.");
    await loadLogs();
  } catch (error) {
    showNotice(logStatus, displayError(error), "error");
  } finally {
    button.disabled = false;
  }
}

function logRow(session) {
  const row = element("li", "record");
  const open = actionButton("", "record-open");
  open.dataset.sessionId = session.session_id;
  open.append(
    element("strong", "", session.session_id),
    element("span", "record-meta", `마지막 실행 ${formatTimestamp(session.last_ts)}`),
    element("span", "record-meta", `이벤트 ${session.event_count} · 재계획 ${session.replan_count} · 오류 ${session.error_count}`),
  );
  open.addEventListener("click", () => openLog(session.session_id));
  const actions = element("div", "form-actions");
  const remove = actionButton("삭제", "danger");
  remove.addEventListener("click", () => removeLog(session.session_id, remove));
  actions.append(remove);
  row.append(open, actions);
  return row;
}

async function loadLogs() {
  const version = ++logListVersion;
  try {
    const sessions = await apiRequest("/api/logs");
    if (version !== logListVersion) return;
    if (sessions.length) logList.replaceChildren(...sessions.map(logRow));
    else logList.replaceChildren(element("li", "muted", "저장된 실행 로그가 없습니다."));
    updateSelectedLog();
  } catch (error) {
    if (version === logListVersion) {
      logList.replaceChildren(element("li", "notice error", displayError(error)));
    }
  }
}

loadLogs();
