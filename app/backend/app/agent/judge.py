def hhmm_to_min(value: str) -> int:
    hours, minutes = map(int, value.split(":"))
    return hours * 60 + minutes


def min_to_hhmm(value: int) -> str:
    hours, minutes = divmod(value, 60)
    return f"{hours:02d}:{minutes:02d}"


def required_minutes(runtime: int, ad: int) -> int:
    return runtime + ad


def evaluate(detail_data: dict, conditions: dict, ad_minutes: int) -> dict:
    runtime = detail_data.get("runtime_min")
    result = {
        "movie_nm": detail_data.get("movie_nm"),
        "runtime_min": runtime,
        "required_min": None,
        "latest_start": None,
        "fits": None,
        "reason": "러닝타임 확인 불가",
    }
    if type(runtime) is not int or runtime <= 0:
        return result

    needed = required_minutes(runtime, ad_minutes)
    result["required_min"] = needed
    end_by, start_after = conditions.get("end_by"), conditions.get("start_after")
    if end_by is None:
        return {**result, "fits": True, "reason": f"필요 시간 {needed}분, 종료 시각 제약 없음"}

    deadline = hhmm_to_min(end_by)
    latest = deadline - needed
    if latest >= 0:
        result["latest_start"] = min_to_hhmm(latest)
    if start_after is None:
        if latest < 0:
            return {
                **result, "fits": False,
                "reason": f"필요 시간 {needed}분으로는 자정부터 시작해도 {end_by}까지 끝낼 수 없습니다.",
            }
        return {**result, "reason": "희망 시작 시각 확인 필요"}

    finish = hhmm_to_min(start_after) + needed
    days, clock = divmod(finish, 24 * 60)
    finish_text = min_to_hhmm(clock)
    if days == 1:
        finish_text = f"다음 날 {finish_text}"
    elif days > 1:
        finish_text = f"{days}일 뒤 {finish_text}"
    fits = latest >= 0 and finish <= deadline
    verdict = "충족" if fits else "불충족"
    reason = f"필요 시간 {needed}분. {start_after}에 시작할 경우 {finish_text} 종료, {end_by} 종료 제약 {verdict}"
    return {**result, "fits": fits, "reason": reason}
