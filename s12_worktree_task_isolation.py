#!/usr/bin/env python3
# Harness: worktree task isolation -- parallel execution lanes via git worktrees.
"""
s12_worktree_task_isolation.py - Standalone Worktree + Task Isolation System

Provides directory-level parallel execution isolation using git worktrees.
Key insight: "Isolate by directory, coordinate by task ID."

Features:
  1. Git worktree lifecycle management (create, track, clean)
  2. Task-worktree binding (tasks as control plane, worktrees as execution plane)
  3. Event logging for observability
  4. Parallel execution lane management

Kept separate from s_full.py by design ("taught separately") to avoid
complexity explosion in the integrated reference.
"""

import os
import sys
import json
import time
import uuid
import shutil
import subprocess
from datetime import datetime
from typing import Optional, List, Dict

# ─── Configuration ───────────────────────────────────────────────────────────

WORKTREE_BASE = os.environ.get("WORKTREE_BASE", ".worktrees")
EVENT_LOG = os.environ.get("EVENT_LOG", ".worktrees/events.jsonl")

# ─── Event Logging ───────────────────────────────────────────────────────────

def log_event(event_type: str, task_id: str, details: dict) -> dict:
    """Log an event for observability."""
    os.makedirs(os.path.dirname(EVENT_LOG), exist_ok=True)

    event = {
        "id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": event_type,
        "task_id": task_id,
        "timestamp": datetime.utcnow().isoformat(),
        "details": details
    }

    with open(EVENT_LOG, 'a') as f:
        f.write(json.dumps(event) + "\n")

    return event

def read_events(task_id: Optional[str] = None, event_type: Optional[str] = None) -> list:
    """Read events, optionally filtered."""
    if not os.path.exists(EVENT_LOG):
        return []

    events = []
    with open(EVENT_LOG, 'r') as f:
        for line in f:
            if line.strip():
                evt = json.loads(line)
                if task_id and evt.get("task_id") != task_id:
                    continue
                if event_type and evt.get("type") != event_type:
                    continue
                events.append(evt)

    return events

# ─── Git Worktree Management ─────────────────────────────────────────────────

def git_worktree_list() -> list:
    """List all existing git worktrees."""
    result = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        capture_output=True, text=True
    )

    worktrees = []
    current = {}
    for line in result.stdout.split("\n"):
        line = line.strip()
        if line.startswith("worktree "):
            if current:
                worktrees.append(current)
            current = {"path": line[9:], "branch": None, "head": None, "bare": False}
        elif line.startswith("branch "):
            current["branch"] = line[7:]
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line == "bare":
            current["bare"] = True

    if current:
        worktrees.append(current)

    return worktrees

def create_worktree(worktree_name: str, branch: Optional[str] = None) -> dict:
    """Create a new git worktree for isolated execution."""
    worktree_path = os.path.join(WORKTREE_BASE, worktree_name)

    # Ensure base directory exists
    os.makedirs(WORKTREE_BASE, exist_ok=True)

    # Check if worktree already exists
    existing = git_worktree_list()
    for wt in existing:
        if wt["path"] == os.path.abspath(worktree_path):
            return {
                "success": False,
                "error": f"Worktree already exists at {worktree_path}",
                "worktree_path": worktree_path
            }

    # Create branch name if not provided
    if not branch:
        branch = f"wt/{worktree_name}"

    # Create the worktree
    cmd = ["git", "worktree", "add", worktree_path]
    if branch:
        # Check if branch exists
        branch_check = subprocess.run(
            ["git", "branch", "--list", branch],
            capture_output=True, text=True
        )
        if not branch_check.stdout.strip():
            cmd.append("-b")
        cmd.append(branch)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr,
            "worktree_path": worktree_path
        }

    log_event("worktree_created", "system", {
        "worktree_name": worktree_name,
        "worktree_path": worktree_path,
        "branch": branch
    })

    return {
        "success": True,
        "worktree_path": worktree_path,
        "branch": branch,
        "command": " ".join(cmd)
    }

def remove_worktree(worktree_name: str, force: bool = False) -> dict:
    """Remove a git worktree."""
    worktree_path = os.path.join(WORKTREE_BASE, worktree_name)

    if not os.path.exists(worktree_path):
        return {
            "success": False,
            "error": f"Worktree not found: {worktree_path}"
        }

    cmd = ["git", "worktree", "remove"]
    if force:
        cmd.append("--force")
    cmd.append(worktree_path)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return {
            "success": False,
            "error": result.stderr
        }

    log_event("worktree_removed", "system", {
        "worktree_name": worktree_name,
        "worktree_path": worktree_path
    })

    return {
        "success": True,
        "worktree_path": worktree_path
    }

def clean_orphan_worktrees() -> dict:
    """Remove worktrees that no longer have valid git metadata."""
    worktrees = git_worktree_list()
    removed = []
    errors = []

    for wt in worktrees:
        path = wt["path"]
        git_file = os.path.join(path, ".git")

        # Check if .git file/link exists and is valid
        if not os.path.exists(git_file):
            # Try to remove via git
            result = subprocess.run(
                ["git", "worktree", "remove", "--force", path],
                capture_output=True, text=True
            )
            if result.returncode == 0:
                removed.append(path)
            else:
                # Fallback: manual cleanup
                try:
                    shutil.rmtree(path, ignore_errors=True)
                    removed.append(path)
                except Exception as e:
                    errors.append({"path": path, "error": str(e)})

    log_event("worktree_cleanup", "system", {
        "removed": removed,
        "errors": errors
    })

    return {
        "success": True,
        "removed": removed,
        "errors": errors
    }

# ─── Task-Worktree Binding ───────────────────────────────────────────────────

TASK_WORKTREE_MAP = {}  # task_id -> {worktree_name, worktree_path, status}

def bind_task_to_worktree(task_id: str, worktree_name: Optional[str] = None) -> dict:
    """Bind a task to a new or existing worktree."""
    if not worktree_name:
        worktree_name = f"task_{task_id}_{uuid.uuid4().hex[:6]}"

    # Create worktree if it doesn't exist
    worktree_path = os.path.join(WORKTREE_BASE, worktree_name)
    if not os.path.exists(worktree_path):
        result = create_worktree(worktree_name)
        if not result["success"]:
            return result

    TASK_WORKTREE_MAP[task_id] = {
        "task_id": task_id,
        "worktree_name": worktree_name,
        "worktree_path": os.path.abspath(worktree_path),
        "status": "bound",
        "bound_at": datetime.utcnow().isoformat()
    }

    log_event("task_bound", task_id, {
        "worktree_name": worktree_name,
        "worktree_path": worktree_path
    })

    return {
        "success": True,
        "task_id": task_id,
        "worktree_name": worktree_name,
        "worktree_path": os.path.abspath(worktree_path)
    }

def unbind_task(task_id: str, remove_worktree_too: bool = False) -> dict:
    """Unbind a task from its worktree."""
    if task_id not in TASK_WORKTREE_MAP:
        return {"success": False, "error": f"Task {task_id} not bound"}

    binding = TASK_WORKTREE_MAP[task_id]
    worktree_name = binding["worktree_name"]

    del TASK_WORKTREE_MAP[task_id]

    log_event("task_unbound", task_id, {
        "worktree_name": worktree_name
    })

    if remove_worktree_too:
        return remove_worktree(worktree_name, force=True)

    return {"success": True, "task_id": task_id, "worktree_name": worktree_name}

def get_task_worktree(task_id: str) -> Optional[dict]:
    """Get worktree info for a task."""
    return TASK_WORKTREE_MAP.get(task_id)

def execute_in_worktree(task_id: str, command: str, env: Optional[dict] = None) -> dict:
    """Execute a command in a task's worktree."""
    binding = get_task_worktree(task_id)
    if not binding:
        return {"success": False, "error": f"Task {task_id} not bound to worktree"}

    worktree_path = binding["worktree_path"]

    # Prepare environment
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    run_env["TASK_ID"] = task_id
    run_env["WORKTREE_PATH"] = worktree_path

    start_time = time.time()
    result = subprocess.run(
        command,
        shell=True,
        cwd=worktree_path,
        capture_output=True,
        text=True,
        env=run_env
    )
    duration = time.time() - start_time

    log_event("task_executed", task_id, {
        "command": command,
        "exit_code": result.returncode,
        "duration_sec": round(duration, 3)
    })

    return {
        "success": result.returncode == 0,
        "task_id": task_id,
        "worktree_path": worktree_path,
        "command": command,
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "duration_sec": round(duration, 3)
    }

# ─── Parallel Execution Lanes ────────────────────────────────────────────────

def create_execution_lane(lane_name: str, task_ids: List[str]) -> dict:
    """Create a parallel execution lane with dedicated worktrees."""
    lane_path = os.path.join(WORKTREE_BASE, f"lane_{lane_name}")
    os.makedirs(lane_path, exist_ok=True)

    bindings = []
    errors = []

    for task_id in task_ids:
        worktree_name = f"lane_{lane_name}_{task_id}"
        result = bind_task_to_worktree(task_id, worktree_name)
        if result["success"]:
            bindings.append(result)
        else:
            errors.append({"task_id": task_id, "error": result.get("error")})

    log_event("lane_created", "system", {
        "lane_name": lane_name,
        "task_count": len(task_ids),
        "bindings": len(bindings),
        "errors": len(errors)
    })

    return {
        "success": len(errors) == 0,
        "lane_name": lane_name,
        "lane_path": lane_path,
        "bindings": bindings,
        "errors": errors
    }

def destroy_execution_lane(lane_name: str, force: bool = False) -> dict:
    """Destroy an execution lane and all its worktrees."""
    prefix = f"lane_{lane_name}_"
    removed = []
    errors = []

    for task_id, binding in list(TASK_WORKTREE_MAP.items()):
        if binding["worktree_name"].startswith(prefix):
            result = unbind_task(task_id, remove_worktree_too=True)
            if result["success"]:
                removed.append(task_id)
            else:
                errors.append({"task_id": task_id, "error": result.get("error")})

    # Clean up lane directory
    lane_path = os.path.join(WORKTREE_BASE, f"lane_{lane_name}")
    if os.path.exists(lane_path):
        shutil.rmtree(lane_path, ignore_errors=True)

    log_event("lane_destroyed", "system", {
        "lane_name": lane_name,
        "removed": removed,
        "errors": errors
    })

    return {
        "success": len(errors) == 0,
        "lane_name": lane_name,
        "removed": removed,
        "errors": errors
    }

# ─── Status and Observability ────────────────────────────────────────────────

def get_system_status() -> dict:
    """Get complete system status."""
    worktrees = git_worktree_list()
    events = read_events()

    return {
        "worktree_base": WORKTREE_BASE,
        "total_worktrees": len(worktrees),
        "worktrees": worktrees,
        "bound_tasks": len(TASK_WORKTREE_MAP),
        "task_bindings": list(TASK_WORKTREE_MAP.values()),
        "total_events": len(events),
        "recent_events": events[-10:] if events else []
    }

def get_task_status(task_id: str) -> dict:
    """Get detailed status for a task."""
    binding = get_task_worktree(task_id)
    events = read_events(task_id=task_id)

    return {
        "task_id": task_id,
        "bound": binding is not None,
        "binding": binding,
        "events": events,
        "event_count": len(events)
    }

# ─── Demo / Self-Test ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("S12：工作树任务隔离测试框架")
    print("=" * 60)

    # 检查 git 仓库
    git_check = subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, text=True)
    if git_check.returncode != 0:
        print("\n错误：当前目录不是 git 仓库，本框架需要在 git 仓库中运行。")
        print("请在 git 仓库根目录下执行。")
        sys.exit(1)

    print(f"\n检测到 Git 仓库：{git_check.stdout.strip()}")
    print(f"工作树根目录：{WORKTREE_BASE}")

    # 列出已有工作树
    print("\n--- 已有工作树 ---")
    existing = git_worktree_list()
    print(f"共找到 {len(existing)} 个工作树")
    for wt in existing:
        print(f"  {wt['path']} ({wt.get('branch', '游离HEAD')})")

    # 创建测试工作树
    print("\n--- 创建测试工作树 ---")
    test_wt = create_worktree("test_harness")
    print(f"结果：{test_wt}")

    # 绑定任务
    print("\n--- 绑定任务到工作树 ---")
    test_task = "task_test_001"
    binding = bind_task_to_worktree(test_task, "test_harness")
    print(f"绑定信息：{binding}")

    # 在工作树中执行命令
    print("\n--- 在工作树中执行命令 ---")
    exec_result = execute_in_worktree(test_task, "pwd && git status --short")
    print(f"退出码：{exec_result['exit_code']}")
    print(f"标准输出：{exec_result['stdout'][:200]}")

    # 查看事件日志
    print("\n--- 事件日志 ---")
    events = read_events()
    print(f"事件总数：{len(events)}")
    for evt in events:
        print(f"  [{evt['type']}] {evt['task_id']} @ {evt['timestamp']}")

    # 系统状态
    print("\n--- 系统状态 ---")
    status = get_system_status()
    print(f"工作树数量：{status['total_worktrees']}")
    print(f"已绑定任务数：{status['bound_tasks']}")

    # 清理
    print("\n--- 清理 ---")
    unbind = unbind_task(test_task, remove_worktree_too=True)
    print(f"解绑结果：{unbind}")

    print("\n" + "=" * 60)
    print("S12 框架就绪，可用于并行执行隔离")
    print("核心理念：目录隔离，任务 ID 协调")
    print("=" * 60)
