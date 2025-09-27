import os, re, json, uuid, requests
from datetime import datetime
from typing import Dict, List

# --- Regex for repo parsing ---
OWNER_REPO_RE = re.compile(r"^([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:\.git)?$")
HTTPS_RE      = re.compile(r"^https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?(?:/|$)")
SSH_RE        = re.compile(r"^git@github\.com:([^/]+)/([^/]+?)(?:\.git)?$")

def make_headers(token: str) -> Dict[str, str]:
    h = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    if token: h['Authorization'] = f'Bearer {token}'
    return h

def gh_get(url: str, token: str, **kw): return requests.get(url, headers=make_headers(token), timeout=30, **kw)
def gh_post(url: str, payload: dict, token: str): return requests.post(url, headers=make_headers(token), json=payload, timeout=60)

def parse_owner_repo_any(s: str):
    s = (s or "").strip()
    for regex in (OWNER_REPO_RE, HTTPS_RE, SSH_RE):
        m = regex.match(s)
        if m: return m.group(1), m.group(2).removesuffix(".git")
    return None, None

def _next_link(headers: dict):
    link = headers.get("Link")
    if not link: return None
    for part in link.split(","):
        if 'rel="next"' in part:
            return part[part.find("<")+1: part.find(">")]
    return None

def normalize_github_url_and_ref(url: str, ref_input: str):
    url, ref_in = (url or "").strip(), (ref_input or "").strip()
    ref_in = ref_in.replace("refs/heads/","").replace("refs/tags/","")
    base_url, detected_ref = url, ""
    if url.startswith("https://github.com/"):
        if "/tree/" in url:
            detected_ref = url.split("/tree/",1)[1].split("/",1)[0]
            base_url = url.split("/tree/",1)[0]
        elif "/commit/" in url:
            detected_ref = url.split("/commit/",1)[1].split("/",1)[0]
            base_url = url.split("/commit/",1)[0]
        elif "/releases/tag/" in url:
            detected_ref = url.split("/releases/tag/",1)[1].split("/",1)[0]
            base_url = url.split("/releases/tag/",1)[0]
        if not base_url.endswith(".git"):
            base_url = base_url.rstrip("/") + ".git"
    return base_url, ref_in or detected_ref, {"detected_ref": detected_ref}

def fetch_all_branches(owner, repo, token):
    out, url = [], f"https://api.github.com/repos/{owner}/{repo}/branches?per_page=100"
    while url:
        r = gh_get(url, token)
        if not r.ok: return out, f"{r.status_code}: {r.text}"
        out += [b["name"] for b in r.json() if "name" in b]
        url = _next_link(r.headers)
    return (["main"]+[b for b in out if b!="main"]) if "main" in out else out, None

def fetch_all_tags(owner, repo, token):
    out, url = [], f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=100"
    while url:
        r = gh_get(url, token)
        if not r.ok: return out, f"{r.status_code}: {r.text}"
        out += [t["name"] for t in r.json() if "name" in t]
        url = _next_link(r.headers)
    return out, None

def list_workflow_runs(owner, repo, workflow_file, branch, token, per_page=30):
    url = f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs"
    return gh_get(url, token, params={"per_page":per_page,"event":"workflow_dispatch","branch":branch})

def find_run_by_tag(runs: List[dict], tag: str):
    for r in runs:
        if tag and tag in (r.get("display_title") or r.get("name") or ""):
            return r
    return runs[0] if runs else None

def get_run_artifacts(owner, repo, run_id, token):
    return gh_get(f"https://api.github.com/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts", token)

def download_artifact_zip(owner, repo, artifact_id, token):
    r = gh_get(f"https://api.github.com/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/zip", token, stream=True)
    return r.content if r.ok else b""

def new_client_tag() -> str:
    return datetime.utcnow().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6]
