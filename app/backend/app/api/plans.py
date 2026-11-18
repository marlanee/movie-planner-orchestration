from fastapi import APIRouter, HTTPException, Response

from ..schemas import PlanIn, PlanOut, PlanSaveResult, PlanUpdate, WatchDate
from ..store import plans_repo


router = APIRouter(prefix="/api/plans", tags=["plans"])
NOT_FOUND = "그런 관람 계획이 없습니다."


def _save_result(plan: dict, overlaps: list[dict]) -> PlanSaveResult:
    warning = None
    if overlaps:
        slots = ", ".join(
            f"{item['movie_nm']} {item['start_time']}~{item['end_time']}" for item in overlaps
        )
        warning = f"같은 날 시간이 겹치는 계획이 {len(overlaps)}건 있습니다: {slots} (저장은 완료됨)"
    return PlanSaveResult(plan=plan, overlaps=overlaps, warning=warning)


@router.post("", response_model=PlanSaveResult, status_code=201)
def create_plan(plan: PlanIn) -> PlanSaveResult:
    try:
        saved = plans_repo.create_plan(plan.model_dump(mode="json"))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    return _save_result(*saved)


@router.get("", response_model=list[PlanOut])
def list_plans(watch_date: WatchDate | None = None) -> list[dict]:
    return plans_repo.list_plans(watch_date.isoformat() if watch_date is not None else None)


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan(plan_id: int) -> dict:
    plan = plans_repo.get_plan(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return plan


@router.put("/{plan_id}", response_model=PlanSaveResult)
def update_plan(plan_id: int, plan: PlanUpdate) -> PlanSaveResult:
    try:
        saved = plans_repo.update_plan(plan_id, plan.model_dump(mode="json", exclude_unset=True))
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    if saved is None:
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return _save_result(*saved)


@router.delete("/{plan_id}", status_code=204)
def delete_plan(plan_id: int) -> Response:
    if not plans_repo.delete_plan(plan_id):
        raise HTTPException(status_code=404, detail=NOT_FOUND)
    return Response(status_code=204)
