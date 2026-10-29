"""GitHub integration tools for SavageOps."""

import json
import re
import time
from typing import Any

from github import Auth, Github

from savageops.config import GITHUB_TOKEN, SERVICE_PATH_MAP, TARGET_REPO


def validate_safe_path(path: str) -> None:
    """Validate that path does not traverse directories or target restricted files."""
    normalized = path.replace("\\", "/")
    parts = [part for part in normalized.split("/") if part]
    denied_names = {
        "dockerfile", "requirements.txt", "pyproject.toml", "poetry.lock", "uv.lock",
        "settings.py", "config.py", "auth.py",
    }
    denied_directories = {".github", "migrations", "tests", "test", "settings", "auth"}
    if (
        ".." in parts
        or normalized.startswith("/")
        or any(part.lower() in denied_directories for part in parts)
        or (parts and parts[-1].lower() in denied_names)
    ):
        raise ValueError(f"Restricted or invalid repository file path: {path}")


def resolve_service_path(service: str, path: str) -> tuple[str, str]:
    """Resolve an observed service/path to its configured repository and path prefix."""
    if not SERVICE_PATH_MAP:
        return TARGET_REPO, path
    try:
        mapping = json.loads(SERVICE_PATH_MAP)
        entry = mapping.get(service, {})
        repo = entry.get("repo", TARGET_REPO)
        prefix = entry.get("prefix", "").strip("/")
    except (TypeError, ValueError, AttributeError):
        raise ValueError("SERVICE_PATH_MAP must be a JSON object of service mappings") from None
    mapped_path = f"{prefix}/{path}" if prefix else path
    return repo, mapped_path


def sanitize_markdown(value: str) -> str:
    """Neutralize mentions and links in untrusted incident text before adding it to a PR."""
    value = re.sub(r"(?:https?://|www\.)\S+", "[link removed]", value, flags=re.IGNORECASE)
    return re.sub(r"@(?=[\w-])", "@\u200b", value)


def _get_client_and_repo(repo_name: str | None = None):
    target_repo = repo_name or TARGET_REPO
    if not GITHUB_TOKEN or not target_repo:
        raise ValueError("GITHUB_TOKEN and TARGET_REPO must be set to interact with GitHub.")
    auth = Auth.Token(GITHUB_TOKEN)
    gh = Github(auth=auth)
    return gh, gh.get_repo(target_repo)


def get_file(path: str, ref: str | None = None, repo_name: str | None = None) -> str:
    """Fetch file content from the target repository with path traversal protection."""
    validate_safe_path(path)
    _, repo = _get_client_and_repo(repo_name)
    content_file = repo.get_contents(path, ref=ref or repo.default_branch)
    if isinstance(content_file, list):
        raise ValueError(f"Path is a directory, not a file: {path}")
    return content_file.decoded_content.decode("utf-8")


def get_file_with_sha(path: str, ref: str | None = None, repo_name: str | None = None) -> tuple[str, str]:
    """Fetch a repository file and its blob SHA for stale-draft detection."""
    validate_safe_path(path)
    _, repo = _get_client_and_repo(repo_name)
    content_file = repo.get_contents(path, ref=ref or repo.default_branch)
    if isinstance(content_file, list):
        raise ValueError(f"Path is a directory, not a file: {path}")
    return content_file.decoded_content.decode("utf-8"), content_file.sha


def numbered(text: str) -> str:
    """Format text with 1-indexed line numbers."""
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(text.splitlines()))


def recent_commits(
    path: str, n: int = 5, repo_name: str | None = None, ref: str | None = None
) -> list[dict]:
    """Retrieve recent commit metadata for a specific file path."""
    validate_safe_path(path)
    _, repo = _get_client_and_repo(repo_name)
    commits = []
    for c in repo.get_commits(path=path, sha=ref)[:n]:
        author_name = c.commit.author.name if c.commit.author else "unknown"
        author_date = str(c.commit.author.date) if c.commit.author else "unknown"
        first_line = c.commit.message.splitlines()[0] if c.commit.message else ""
        commits.append(
            {
                "sha": c.sha[:7],
                "full_sha": c.sha,
                "author": author_name,
                "date": author_date,
                "message": first_line,
            }
        )
    return commits


def update_branch_file(path: str, new_content: str, commit_msg: str, branch_name: str) -> str:
    """Commit an updated file to an existing branch and return the new commit SHA."""
    validate_safe_path(path)
    _, repo = _get_client_and_repo()
    file_on_branch = repo.get_contents(path, ref=branch_name)
    res = repo.update_file(
        path=path,
        message=commit_msg,
        content=new_content,
        sha=file_on_branch.sha,
        branch=branch_name,
    )
    return res["commit"].sha


def open_pr(
    path: str, new_content: str, commit_msg: str, pr_title: str, pr_body: str, incident_id: str,
    expected_base_sha: str | None = None, repo_name: str | None = None,
) -> dict[str, str]:
    """Create a new branch, commit the modified file, and open a PR against default branch."""
    validate_safe_path(path)
    _, repo = _get_client_and_repo(repo_name)
    default_branch = repo.get_branch(repo.default_branch)
    current_file = repo.get_contents(path, ref=repo.default_branch)
    if expected_base_sha and current_file.sha != expected_base_sha:
        raise ValueError("Target file changed since the draft was created; regenerate the draft.")
    branch_name = f"ai-fix-{incident_id[:8]}"

    # Create new branch from default branch head
    branch_exists = False
    try:
        repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=default_branch.commit.sha)
    except Exception:
        try:
            repo.get_git_ref(f"heads/{branch_name}")
            branch_exists = True
        except Exception:
            raise
        for pull in repo.get_pulls(state="all", head=f"{repo.owner.login}:{branch_name}"):
            return {"pr_url": pull.html_url, "branch": branch_name,
                    "head_sha": pull.head.sha, "pr_number": str(pull.number)}
        if not branch_exists:
            raise

    # Update file on the new branch
    file_on_branch = repo.get_contents(path, ref=branch_name)
    if expected_base_sha and file_on_branch.sha != expected_base_sha:
        if not branch_exists:
            raise ValueError("Target file changed before branch creation; regenerate the draft.")
    if file_on_branch.decoded_content.decode("utf-8") == new_content:
        head_sha = repo.get_branch(branch_name).commit.sha
    else:
        update_res = repo.update_file(
            path=path,
            message=commit_msg,
            content=new_content,
            sha=file_on_branch.sha,
            branch=branch_name,
        )
        head_sha = update_res["commit"].sha

    # Open PR
    pull = repo.create_pull(
        title=sanitize_markdown(pr_title),
        body=pr_body,
        head=branch_name,
        base=repo.default_branch,
    )
    return {
        "pr_url": pull.html_url,
        "branch": branch_name,
        "head_sha": head_sha,
        "pr_number": str(pull.number),
    }


def get_ci_check_runs(commit_sha: str) -> list[dict[str, Any]]:
    """Fetch GitHub Actions check runs for a commit."""
    _, repo = _get_client_and_repo()
    runs = repo.get_commit(commit_sha).get_check_runs()
    results = []
    for run in runs:
        results.append(
            {
                "name": run.name,
                "status": run.status,
                "conclusion": run.conclusion,
                "title": run.output.get("title") if run.output else None,
                "summary": run.output.get("summary") if run.output else None,
            }
        )
    return results


def wait_for_ci_status(
    commit_sha: str, timeout_seconds: int = 45, poll_interval: int = 5
) -> dict[str, Any]:
    """Poll GitHub Actions check runs until completion or timeout."""
    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        try:
            runs = get_ci_check_runs(commit_sha)
        except Exception as e:
            return {"status": "error", "error": str(e), "runs": []}

        if not runs:
            # No CI checks configured or registered yet
            return {"status": "none", "runs": []}

        all_completed = all(r["status"] == "completed" for r in runs)
        if all_completed:
            failures = [r for r in runs if r["conclusion"] not in ("success", "neutral", "skipped")]
            if failures:
                return {
                    "status": "failure",
                    "runs": runs,
                    "failure_details": "; ".join(
                        f"{f['name']}: {f['conclusion']} ({f['summary'] or ''})" for f in failures
                    ),
                }
            return {"status": "success", "runs": runs}

        time.sleep(poll_interval)

    return {"status": "timeout", "runs": runs if "runs" in locals() else []}
