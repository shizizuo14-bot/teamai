from __future__ import annotations

import json
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "inspect_agents.py"
SKILL = SKILL_ROOT / "SKILL.md"
KNOWLEDGE_RULES = SKILL_ROOT / "references" / "knowledge-rules.md"
OPENAI_CONFIG = SKILL_ROOT / "agents" / "openai.yaml"


def load_script_module():
    spec = importlib.util.spec_from_file_location("inspect_agents", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True
    )
    return result.stdout.strip()


class InspectAgentsTest(unittest.TestCase):
    def make_repo(self) -> tuple[tempfile.TemporaryDirectory[str], Path]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        git(root, "init")
        git(root, "config", "user.email", "skill-test@example.invalid")
        git(root, "config", "user.name", "Skill Test")
        return temp, root

    def run_script(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), str(path)],
            text=True,
            capture_output=True,
        )

    def test_reports_agents_chain_and_git_state(self) -> None:
        temp, root = self.make_repo()
        self.addCleanup(temp.cleanup)
        nested = root / "services" / "quality"
        nested.mkdir(parents=True)
        (root / "AGENTS.md").write_text("root\n", encoding="utf-8")
        (nested.parent / "AGENTS.md").write_text("service\n", encoding="utf-8")
        git(root, "add", "AGENTS.md")
        git(root, "commit", "-m", "test: add root guidance")

        result = self.run_script(nested)

        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(str(root.resolve()), payload["repo_root"])
        self.assertEqual(2, len(payload["agents_files"]))
        self.assertEqual("tracked", payload["agents_files"][0]["git_state"])
        self.assertEqual("untracked", payload["agents_files"][1]["git_state"])
        self.assertEqual(
            sum(item["bytes"] for item in payload["agents_files"]),
            payload["combined_bytes"],
        )

    def test_missing_agents_returns_empty_chain(self) -> None:
        temp, root = self.make_repo()
        self.addCleanup(temp.cleanup)

        result = self.run_script(root)

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([], json.loads(result.stdout)["agents_files"])

    def test_non_git_directory_returns_code_two(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_script(Path(temp))

        self.assertEqual(2, result.returncode)
        self.assertIn("error", json.loads(result.stdout))

    def test_script_does_not_change_git_status(self) -> None:
        temp, root = self.make_repo()
        self.addCleanup(temp.cleanup)
        (root / "AGENTS.md").write_text("root\n", encoding="utf-8")
        before = git(root, "status", "--porcelain=v1", "--untracked-files=all")

        result = self.run_script(root)
        after = git(root, "status", "--porcelain=v1", "--untracked-files=all")

        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(before, after)

    def test_exposes_porcelain_status_parser(self) -> None:
        module = load_script_module()

        self.assertTrue(hasattr(module, "parse_porcelain_status"))

    def test_parses_porcelain_status_details(self) -> None:
        module = load_script_module()
        self.assertTrue(hasattr(module, "parse_porcelain_status"))
        if not hasattr(module, "parse_porcelain_status"):
            return

        cases = (
            ("clean", "", None, None, None, False),
            ("unstaged", " M AGENTS.md\n", " M", None, "M", False),
            ("staged", "M  AGENTS.md\n", "M ", "M", None, False),
            ("mixed", "MM AGENTS.md\n", "MM", "M", "M", False),
            ("ignored", "!! AGENTS.md\n", "!!", "!", "!", False),
            ("conflicted", "UU AGENTS.md\n", "UU", "U", "U", True),
        )

        for name, porcelain, status_code, index_status, worktree_status, conflicted in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    {
                        "status_code": status_code,
                        "index_status": index_status,
                        "worktree_status": worktree_status,
                        "conflicted": conflicted,
                    },
                    module.parse_porcelain_status(porcelain),
                )

    def test_reports_path_status_for_clean_and_untracked_agents_files(self) -> None:
        temp, root = self.make_repo()
        self.addCleanup(temp.cleanup)
        nested = root / "service"
        nested.mkdir()
        (root / "AGENTS.md").write_text("root\n", encoding="utf-8")
        (nested / "AGENTS.md").write_text("service\n", encoding="utf-8")
        git(root, "add", "AGENTS.md")
        git(root, "commit", "-m", "test: add tracked guidance")

        result = self.run_script(nested)

        self.assertEqual(0, result.returncode, result.stderr)
        files = json.loads(result.stdout)["agents_files"]
        self.assertEqual(
            {
                "status_code": None,
                "index_status": None,
                "worktree_status": None,
                "conflicted": False,
            },
            {field: files[0][field] for field in ("status_code", "index_status", "worktree_status", "conflicted")},
        )
        self.assertEqual(
            {
                "status_code": "??",
                "index_status": "?",
                "worktree_status": "?",
                "conflicted": False,
            },
            {field: files[1][field] for field in ("status_code", "index_status", "worktree_status", "conflicted")},
        )

    def test_write_confirmation_does_not_authorize_git_publication(self) -> None:
        contract = (
            "文档写入确认只授权执行已确认的精确补丁，不授权 Git 暂存、提交、"
            "推送或创建 PR；每项版本控制操作都必须由用户另行明确请求或批准。"
        )

        for path in (SKILL, KNOWLEDGE_RULES):
            with self.subTest(path=path.name):
                self.assertIn(contract, path.read_text(encoding="utf-8"))

    def test_user_facing_skill_content_is_chinese(self) -> None:
        skill = SKILL.read_text(encoding="utf-8")
        rules = KNOWLEDGE_RULES.read_text(encoding="utf-8")
        config = OPENAI_CONFIG.read_text(encoding="utf-8")

        self.assertIn("description: 适用于", skill)
        self.assertIn("# 项目知识沉淀", skill)
        self.assertIn("# 项目知识沉淀规则", rules)
        self.assertIn(
            'default_prompt: "使用 $capturing-project-knowledge '
            "评估当前任务是否产生了值得长期保留的项目知识；"
            '仅在确有沉淀价值时，提出有证据支撑的修改建议。"',
            config,
        )
        self.assertNotIn("# Capturing Project Knowledge", skill)
        self.assertNotIn("# Knowledge Capture Rules", rules)


if __name__ == "__main__":
    unittest.main()
