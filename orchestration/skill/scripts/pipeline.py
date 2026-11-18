"""astra-led-pipeline 호출 스크립트 (오케스트레이터 전용)

    python pipeline.py worker --repo <repo> --task-dir <dir>
    python pipeline.py review --repo <repo> --task-dir <dir>
    python pipeline.py astra  --repo <repo> --task-dir <dir> --issue <issue_id>
    python pipeline.py ping   --repo <repo>

각 호출은 입력 양식 파일을 읽어 CLI로 넘기고, 결과를 출력 파일에 쓰고,
<repo>/.omc/pipeline/pipeline_log.jsonl 에 한 줄을 남긴다.
키·토큰은 다루지 않는다. 각 CLI가 이미 로그인된 상태를 그대로 쓴다.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

WORKER = {"model": "gpt-6.2-sol", "effort": "high"}
ASTRA = {"model": "gpt-6.2-astra", "effort": "xhigh"}
REVIEWER = {"model": "claude-opus-5-5", "effort": "xhigh"}
ASTRA_CAP_PER_ISSUE = 2
DEFAULT_TIMEOUT = 1800  # 초

SKILL_DIR = Path(__file__).resolve().parent.parent
PACKETS = SKILL_DIR / "references" / "packets.md"


def find_cli(name: str) -> str:
    path = shutil.which(name)
    if not path:
        sys.exit(f"[pipeline] '{name}' CLI를 찾지 못했습니다. 설치와 PATH를 확인하세요.")
    return path


def log(repo: Path, entry: dict) -> None:
    log_path = repo / ".omc" / "pipeline" / "pipeline_log.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run(cmd: list[str], stdin_text: str, cwd: Path, timeout: int) -> tuple[int, str, str, float]:
    start = time.monotonic()
    try:
        proc = subprocess.run(
            cmd, input=stdin_text, cwd=cwd, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
        )
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        code, out, err = -1, "", f"시간 초과({timeout}초)"
    return code, out, err, round(time.monotonic() - start, 1)


def read_input(task_dir: Path, name: str) -> str:
    path = task_dir / name
    if not path.is_file() or not path.read_text(encoding="utf-8").strip():
        sys.exit(f"[pipeline] 입력 파일이 없거나 비어 있습니다: {path}")
    return path.read_text(encoding="utf-8")


def role_prompt(role: str, body: str) -> str:
    packets = PACKETS.read_text(encoding="utf-8") if PACKETS.is_file() else ""
    return (
        f"[astra-led-pipeline:{role}]\n"
        f"너는 astra-led-pipeline 스킬의 '{role}' 역할이다. 그 스킬의 해당 역할 규칙만 따른다.\n"
        f"응답은 아래 양식 문서의 해당 양식으로만 끝낸다.\n\n"
        f"<양식>\n{packets}\n</양식>\n\n<입력>\n{body}\n</입력>\n"
    )


def codex_call(role: str, spec: dict, sandbox: str, repo: Path, task_dir: Path,
               in_name: str, out_name: str, timeout: int) -> int:
    codex = find_cli("codex")
    out_path = task_dir / out_name
    if out_path.exists():
        out_path.unlink()
    cmd = [codex, "exec", "-m", spec["model"],
           "-c", f'model_reasoning_effort="{spec["effort"]}"',
           "-s", sandbox, "-C", str(repo), "-o", str(out_path), "-"]
    prompt = role_prompt(role, read_input(task_dir, in_name))
    code, out, err, secs = run(cmd, prompt, repo, timeout)
    ok = code == 0 and out_path.is_file() and out_path.read_text(encoding="utf-8").strip() != ""
    (task_dir / f"{role}_stderr.log").write_text(err[-20000:], encoding="utf-8")
    log(repo, {"ts": datetime.now(timezone.utc).isoformat(), "role": role,
               "model": spec["model"], "effort": spec["effort"], "task_dir": str(task_dir),
               "exit_code": code, "ok": ok, "seconds": secs})
    print(f"[pipeline] {role} {'완료' if ok else '실패'} ({secs}s) → {out_path if ok else '응답 없음'}")
    return 0 if ok else 1


def cmd_worker(a) -> int:
    return codex_call("worker", WORKER, "workspace-write", a.repo, a.task_dir,
                      "task_packet.md", "work_result.md", a.timeout)


def cmd_astra(a) -> int:
    state_path = a.task_dir / "astra_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.is_file() else {}
    used = state.get(a.issue, 0)
    if used >= ASTRA_CAP_PER_ISSUE:
        print(f"[pipeline] issue '{a.issue}'는 Astra를 이미 {used}회 호출했습니다. 사용자에게 넘기세요.")
        return 2
    rc = codex_call("astra", ASTRA, "read-only", a.repo, a.task_dir,
                    "escalation_packet.md", "astra_directive.md", a.timeout)
    state[a.issue] = used + 1
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    return rc


def cmd_review(a) -> int:
    claude = find_cli("claude")
    out_path = a.task_dir / "review_result.md"
    cmd = [claude, "-p", "--model", REVIEWER["model"], "--effort", REVIEWER["effort"],
           "--permission-mode", "dontAsk",
           "--allowedTools", "Read Grep Glob Bash",
           "--disallowedTools", "Edit Write NotebookEdit",
           "--no-session-persistence"]
    prompt = role_prompt("reviewer", read_input(a.task_dir, "review_request.md")) + (
        "\n파일을 수정하지 마라. Bash는 읽기와 검증 명령 실행에만 쓴다.\n")
    code, out, err, secs = run(cmd, prompt, a.repo, a.timeout)
    ok = code == 0 and out.strip() != ""
    if ok:
        out_path.write_text(out, encoding="utf-8")
    (a.task_dir / "reviewer_stderr.log").write_text(err[-20000:], encoding="utf-8")
    log(a.repo, {"ts": datetime.now(timezone.utc).isoformat(), "role": "reviewer",
                 "model": REVIEWER["model"], "effort": REVIEWER["effort"],
                 "task_dir": str(a.task_dir), "exit_code": code, "ok": ok, "seconds": secs})
    print(f"[pipeline] reviewer {'완료' if ok else '실패'} ({secs}s) → {out_path if ok else '응답 없음'}")
    return 0 if ok else 1


def cmd_ping(a) -> int:
    """짧은 연결 확인: 각 CLI·모델이 응답하는지만 본다(낮은 effort)."""
    results = {}
    codex, claude = find_cli("codex"), find_cli("claude")
    for name, model in (("sol", WORKER["model"]), ("astra", ASTRA["model"])):
        cmd = [codex, "exec", "-m", model, "-c", 'model_reasoning_effort="low"',
               "-s", "read-only", "-C", str(a.repo), "-"]
        code, out, err, secs = run(cmd, "PONG 한 단어만 답하라.", a.repo, 300)
        results[name] = {"exit_code": code, "seconds": secs, "pong": "PONG" in out.upper(),
                         "stderr_tail": err.strip().splitlines()[-3:] if code else []}
    cmd = [claude, "-p", "--model", REVIEWER["model"], "--effort", "low",
           "--permission-mode", "dontAsk", "--no-session-persistence"]
    code, out, err, secs = run(cmd, "PONG 한 단어만 답하라.", a.repo, 300)
    results["reviewer"] = {"exit_code": code, "seconds": secs, "pong": "PONG" in out.upper(),
                           "stderr_tail": err.strip().splitlines()[-3:] if code else []}
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if all(r["pong"] for r in results.values()) else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔(cp949)에서 한글 깨짐 방지
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    p = argparse.ArgumentParser(description="astra-led-pipeline 호출")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("worker", "review", "astra", "ping"):
        s = sub.add_parser(name)
        s.add_argument("--repo", type=Path, required=True)
        if name != "ping":
            s.add_argument("--task-dir", type=Path, required=True)
        if name == "astra":
            s.add_argument("--issue", required=True)
        s.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    a = p.parse_args()
    a.repo = a.repo.resolve()
    if hasattr(a, "task_dir"):
        a.task_dir = a.task_dir.resolve()
        a.task_dir.mkdir(parents=True, exist_ok=True)
    return {"worker": cmd_worker, "review": cmd_review,
            "astra": cmd_astra, "ping": cmd_ping}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
