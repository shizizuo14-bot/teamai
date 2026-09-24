from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


CONFLICT_STATUS_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def run_git(
    cwd: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=check,
    )


def find_repo_root(start: Path) -> Path:
    cwd = start.parent if start.is_file() else start
    result = run_git(cwd, "rev-parse", "--show-toplevel")
    return Path(result.stdout.strip()).resolve()


def find_agents_files(repo_root: Path, start: Path) -> list[Path]:
    current = start.resolve()
    if current.is_file():
        current = current.parent
    current.relative_to(repo_root)
    directories = [repo_root]
    relative = current.relative_to(repo_root)
    cursor = repo_root
    for part in relative.parts:
        cursor = cursor / part
        directories.append(cursor)
    return [
        directory / "AGENTS.md"
        for directory in directories
        if (directory / "AGENTS.md").is_file()
    ]


def git_state(repo_root: Path, path: Path) -> str:
    relative = str(path.relative_to(repo_root))
    tracked = run_git(
        repo_root, "ls-files", "--error-unmatch", "--", relative, check=False
    )
    if tracked.returncode == 0:
        return "tracked"
    ignored = run_git(repo_root, "check-ignore", "-q", "--", relative, check=False)
    return "ignored" if ignored.returncode == 0 else "untracked"


def parse_porcelain_status(output: str) -> dict[str, str | bool | None]:
    line = next((item for item in output.splitlines() if item), "")
    if not line:
        return {
            "status_code": None,
            "index_status": None,
            "worktree_status": None,
            "conflicted": False,
        }

    status_code = line[:2]
    return {
        "status_code": status_code,
        "index_status": None if status_code[0] == " " else status_code[0],
        "worktree_status": None if status_code[1] == " " else status_code[1],
        "conflicted": status_code in CONFLICT_STATUS_CODES,
    }


def git_path_status(repo_root: Path, path: Path) -> dict[str, str | bool | None]:
    relative = str(path.relative_to(repo_root))
    result = run_git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--ignored",
        "--untracked-files=all",
        "--",
        relative,
    )
    return parse_porcelain_status(result.stdout)


def inspect(start: Path) -> dict[str, Any]:
    resolved = start.resolve()
    repo_root = find_repo_root(resolved)
    files = []
    for path in find_agents_files(repo_root, resolved):
        files.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(repo_root)),
                "bytes": path.stat().st_size,
                "git_state": git_state(repo_root, path),
                **git_path_status(repo_root, path),
            }
        )
    return {
        "repo_root": str(repo_root),
        "start": str(resolved),
        "agents_files": files,
        "combined_bytes": sum(item["bytes"] for item in files),
    }


def main(argv: list[str]) -> int:
    start = Path(argv[1]) if len(argv) > 1 else Path.cwd()
    try:
        payload = inspect(start)
    except (subprocess.CalledProcessError, ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
