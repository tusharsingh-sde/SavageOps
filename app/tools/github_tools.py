"""GitHub integration tools for SavageOps."""

from github import Auth, Github

from app.config import GITHUB_TOKEN, TARGET_REPO


def _get_client_and_repo():
    if not GITHUB_TOKEN or not TARGET_REPO:
        raise ValueError("GITHUB_TOKEN and TARGET_REPO must be set to interact with GitHub.")
    auth = Auth.Token(GITHUB_TOKEN)
    gh = Github(auth=auth)
    return gh, gh.get_repo(TARGET_REPO)


def get_file(path: str, ref: str | None = None) -> str:
    """Fetch file content from the target repository with path traversal protection."""
    if ".." in path or path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"Invalid repository file path: {path}")
    _, repo = _get_client_and_repo()
    content_file = repo.get_contents(path, ref=ref or repo.default_branch)
    if isinstance(content_file, list):
        raise ValueError(f"Path is a directory, not a file: {path}")
    return content_file.decoded_content.decode("utf-8")


def numbered(text: str) -> str:
    """Format text with 1-indexed line numbers."""
    return "\n".join(f"{i + 1}: {line}" for i, line in enumerate(text.splitlines()))


def recent_commits(path: str, n: int = 5) -> list[dict]:
    """Retrieve recent commit metadata for a specific file path."""
    if ".." in path or path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"Invalid repository file path: {path}")
    _, repo = _get_client_and_repo()
    commits = []
    for c in repo.get_commits(path=path)[:n]:
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


def open_pr(
    path: str, new_content: str, commit_msg: str, pr_title: str, pr_body: str, incident_id: str
) -> str:
    """Create a new branch, commit the modified file, and open a PR against default branch."""
    if ".." in path or path.startswith("/") or path.startswith("\\"):
        raise ValueError(f"Invalid repository file path: {path}")
    _, repo = _get_client_and_repo()
    default_branch = repo.get_branch(repo.default_branch)
    branch_name = f"ai-fix-{incident_id[:8]}"

    # Create new branch from default branch head
    repo.create_git_ref(ref=f"refs/heads/{branch_name}", sha=default_branch.commit.sha)

    # Update file on the new branch
    file_on_branch = repo.get_contents(path, ref=branch_name)
    repo.update_file(
        path=path,
        message=commit_msg,
        content=new_content,
        sha=file_on_branch.sha,
        branch=branch_name,
    )

    # Open PR
    pull = repo.create_pull(
        title=pr_title,
        body=pr_body,
        head=branch_name,
        base=repo.default_branch,
    )
    return pull.html_url
