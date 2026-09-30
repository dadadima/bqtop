"""Small Resource Manager helpers used by the wizard and `--check`: who is the parent of a project, what
folders sit above it, which projects live directly in a folder. gcloud first, then the REST API with the
machine's Google credentials. Everything is best effort and returns empty values when it cannot know."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

_RM = "https://cloudresourcemanager.googleapis.com/v3"


@dataclass
class Node:
    kind: str  # folder | organization
    id: str
    name: str = ""

    @property
    def label(self) -> str:
        return f"{self.name} ({self.id})" if self.name else f"{self.kind} {self.id}"


def _session():
    import google.auth
    from google.auth.transport.requests import AuthorizedSession

    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    return AuthorizedSession(creds)


def _gcloud(*args: str) -> str:
    try:
        r = subprocess.run(["gcloud", *args], capture_output=True, text=True, timeout=8, check=False)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def default_project() -> str:
    out = _gcloud("config", "get-value", "project", "--format=value(.)")
    if out and out != "(unset)":
        return out
    try:
        import google.auth

        _, project = google.auth.default()
        return project or ""
    except Exception:
        return ""


def parent_of_project(project: str) -> Node | None:
    out = _gcloud("projects", "describe", project, "--format=value(parent.type,parent.id)").split()
    if len(out) == 2:
        node = Node(out[0], out[1])
    else:
        try:
            r = _session().get(f"{_RM}/projects/{project}", timeout=8)
            parent = r.json().get("parent", "") if r.status_code == 200 else ""
            if "/" not in parent:
                return None
            kind, pid = parent.split("/", 1)
            node = Node({"folders": "folder", "organizations": "organization"}.get(kind, kind), pid)
        except Exception:
            return None
    if node.kind == "folder":
        node.name = folder_name(node.id)
    return node


def folder_name(folder_id: str) -> str:
    out = _gcloud("resource-manager", "folders", "describe", folder_id, "--format=value(displayName)")
    if out:
        return out
    try:
        r = _session().get(f"{_RM}/folders/{folder_id}", timeout=8)
        return r.json().get("displayName", "") if r.status_code == 200 else ""
    except Exception:
        return ""


def parent_of_folder(folder_id: str) -> Node | None:
    out = _gcloud("resource-manager", "folders", "describe", folder_id, "--format=value(parent)")
    parent = out
    if not parent:
        try:
            r = _session().get(f"{_RM}/folders/{folder_id}", timeout=8)
            parent = r.json().get("parent", "") if r.status_code == 200 else ""
        except Exception:
            return None
    if "/" not in parent:
        return None
    kind, pid = parent.split("/", 1)
    node = Node({"folders": "folder", "organizations": "organization"}.get(kind, kind), pid)
    if node.kind == "folder":
        node.name = folder_name(node.id)
    return node


def ancestors(project: str, max_depth: int = 6) -> list[Node]:
    """Folders above a project, nearest first, ending with the organization if reachable."""
    chain: list[Node] = []
    node = parent_of_project(project)
    while node and len(chain) < max_depth:
        chain.append(node)
        if node.kind != "folder":
            break
        node = parent_of_folder(node.id)
    return chain


def projects_in_folder(folder_id: str) -> list[str]:
    out = _gcloud("projects", "list", f"--filter=parent.id={folder_id}", "--format=value(projectId)")
    if out:
        return out.split()
    try:
        r = _session().get(f"{_RM}/projects", params={"parent": f"folders/{folder_id}", "pageSize": 50}, timeout=8)
        return [p["projectId"] for p in r.json().get("projects", [])] if r.status_code == 200 else []
    except Exception:
        return []


def can_run_jobs(project: str) -> bool:
    """Dry-run a trivial query: checks bigquery.jobs.create in the project with the current credentials."""
    try:
        from google.cloud import bigquery

        bigquery.Client(project=project).query("select 1", job_config=bigquery.QueryJobConfig(dry_run=True))
        return True
    except Exception:
        return False


def find_runner(folder_id: str, regions: tuple[str, ...] = ("us", "eu")) -> tuple[str, str]:
    """A project directly inside the folder from which JOBS_BY_FOLDER works for the current identity
    (jobs.create there + jobs.listAll on the folder). Returns (project, region); ("", "") when none works
    and ("", "empty") when the folder has no projects directly inside it."""
    try:
        from google.cloud import bigquery
    except ImportError:
        return "", ""
    candidates = projects_in_folder(folder_id)
    if not candidates:
        return "", "empty"
    for project in candidates[:8]:
        for region in regions:
            sql = (
                f"select 1 from `{project}`.`region-{region}`.INFORMATION_SCHEMA.JOBS_BY_FOLDER "
                "where creation_time > current_timestamp() limit 1"
            )
            try:
                bigquery.Client(project=project).query(sql, job_config=bigquery.QueryJobConfig(dry_run=True))
                return project, region
            except Exception:
                continue
    return "", ""
