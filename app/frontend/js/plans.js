"use strict";

const planList = document.querySelector("#plan-list");
const dateFilter = document.querySelector("#filter-date");
const planStatus = document.querySelector("#plan-status");
const planDetail = document.querySelector("#plan-detail");
const detailBody = document.querySelector("#plan-detail-body");
const formTemplate = document.querySelector("#plan-form-template");
let selectedPlanId = null;
let listVersion = 0;
let detailVersion = 0;

function showPlan(plan) {
  const details = element("dl", "details");
  for (const [label, value] of [
    ["영화명", plan.movie_nm], ["관람 날짜", plan.watch_date],
    ["관람 시간", `${plan.start_time}~${plan.end_time}`],
    ["영화관", plan.theater_name], ["주소", plan.theater_address],
    ["메모", plan.memo], ["생성 일시", formatTimestamp(plan.created_at)],
    ["수정 일시", formatTimestamp(plan.updated_at)],
  ]) {
    details.append(element("dt", "", label), element("dd", "", value || "—"));
  }
  planDetail.hidden = false;
  detailBody.replaceChildren(details);
}

async function openPlan(planId) {
  const version = ++detailVersion;
  selectedPlanId = planId;
  planDetail.hidden = false;
  detailBody.replaceChildren(element("p", "muted", "계획을 불러오는 중…"));
  try {
    const plan = await apiRequest(`/api/plans/${planId}`);
    if (version === detailVersion) showPlan(plan);
  } catch (error) {
    if (version === detailVersion) {
      detailBody.replaceChildren(element("p", "notice error", displayError(error)));
    }
  }
}

function makePlanForm(plan = null) {
  const form = formTemplate.content.firstElementChild.cloneNode(true);
  const submitButton = form.querySelector('button[type="submit"]');
  const feedback = element("p", "notice");
  feedback.setAttribute("role", "status");
  feedback.hidden = true;
  form.append(feedback);

  if (plan) {
    const title = form.elements.namedItem("movie_nm");
    title.disabled = true;
    title.parentElement.hidden = true;
    for (const field of ["watch_date", "start_time", "end_time", "theater_name", "theater_address", "memo"]) {
      form.elements.namedItem(field).value = plan[field] ?? "";
    }
    const cancel = actionButton("취소");
    cancel.addEventListener("click", () => form.remove());
    form.querySelector(".form-actions").append(cancel);
  }

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (submitButton.disabled) return;
    const fields = new FormData(form);
    const body = {
      watch_date: fields.get("watch_date"),
      start_time: fields.get("start_time"),
      end_time: fields.get("end_time"),
      theater_name: fields.get("theater_name").trim() || null,
      theater_address: fields.get("theater_address").trim() || null,
      memo: fields.get("memo").trim() || null,
    };
    if (!plan) body.movie_nm = fields.get("movie_nm").trim();
    submitButton.disabled = true;
    showNotice(feedback);
    showNotice(planStatus);
    try {
      const saved = await apiRequest(plan ? `/api/plans/${plan.plan_id}` : "/api/plans", {
        method: plan ? "PUT" : "POST", body,
      });
      if (selectedPlanId === saved.plan.plan_id) {
        detailVersion++;
        showPlan(saved.plan);
      }
      if (plan) form.remove();
      else form.reset();
      const success = plan ? "관람 계획을 수정했습니다." : "관람 계획을 저장했습니다.";
      showNotice(planStatus, saved.warning || success, saved.warning ? "warning" : "");
      await loadPlans();
    } catch (error) {
      showNotice(feedback, displayError(error), "error");
    } finally {
      submitButton.disabled = false;
    }
  });
  return form;
}

async function removePlan(plan, button) {
  if (!window.confirm(`${plan.movie_nm} 관람 계획을 삭제할까요?`)) return;
  button.disabled = true;
  try {
    await apiRequest(`/api/plans/${plan.plan_id}`, { method: "DELETE" });
    if (selectedPlanId === plan.plan_id) {
      selectedPlanId = null;
      detailVersion++;
      planDetail.hidden = true;
      detailBody.replaceChildren();
    }
    showNotice(planStatus, "관람 계획을 삭제했습니다.");
    await loadPlans();
  } catch (error) {
    showNotice(planStatus, displayError(error), "error");
  } finally {
    button.disabled = false;
  }
}

function planRow(plan) {
  const row = element("li", "record");
  const open = actionButton("", "record-open");
  open.append(
    element("strong", "", plan.movie_nm),
    element("span", "record-meta", `${plan.watch_date} · ${plan.start_time}~${plan.end_time}`),
    element("span", "record-meta", `${plan.theater_name || "영화관 미지정"} · 생성 ${formatTimestamp(plan.created_at)}`),
  );
  open.addEventListener("click", () => openPlan(plan.plan_id));

  const actions = element("div", "form-actions");
  const edit = actionButton("수정");
  edit.addEventListener("click", () => {
    if (row.querySelector("form")) return;
    const form = makePlanForm(plan);
    row.append(form);
    form.elements.namedItem("watch_date").focus();
  });
  const remove = actionButton("삭제", "danger");
  remove.addEventListener("click", () => removePlan(plan, remove));
  actions.append(edit, remove);
  row.append(open, actions);
  return row;
}

async function loadPlans() {
  const version = ++listVersion;
  const query = dateFilter.value ? `?${new URLSearchParams({ watch_date: dateFilter.value })}` : "";
  try {
    const plans = await apiRequest(`/api/plans${query}`);
    if (version !== listVersion) return;
    if (plans.length) planList.replaceChildren(...plans.map(planRow));
    else planList.replaceChildren(element("li", "muted", "표시할 관람 계획이 없습니다."));
  } catch (error) {
    if (version === listVersion) {
      planList.replaceChildren(element("li", "notice error", displayError(error)));
    }
  }
}

dateFilter.addEventListener("change", loadPlans);
document.querySelector("#add-plan").append(makePlanForm());
loadPlans();
