from typing import Dict, Any
from .github_api import gh_post, list_workflow_runs, find_run_by_tag, get_run_artifacts, download_artifact_zip, normalize_github_url_and_ref

# All workflows live in OSS_Compliance/.github/workflows
DEFAULTS = {
    'owner': 'Bharathnelle335',
    'repo': 'OSS_Compliance',
    'workflow_file': 'scanoss.yml',
    'branch': 'main',
}

def build_inputs(scan_type, source, image_scan_mode, git_ref, enable_scanoss, client_run_id) -> Dict[str,Any]:
    inputs = {
        "scan_type": scan_type,
        "image_scan_mode": image_scan_mode,
        "docker_image": source if scan_type=="docker" else "",
        "git_url": source if scan_type=="git" else "",
        "git_ref": git_ref if scan_type=="git" else "",
        "archive_url": source if scan_type in ("upload-zip","upload-tar") else "",
        "enable_scanoss": "true" if enable_scanoss else "false",
        "client_run_id": client_run_id,
    }
    if scan_type=="git" and inputs["git_url"] and not inputs["git_ref"]:
        _,detected,_ = normalize_github_url_and_ref(inputs["git_url"],"")
        if detected: inputs["git_ref"]=detected
    return inputs

def trigger(owner, repo, wf_file, branch, inputs, token):
    url=f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/{wf_file}/dispatches"
    return gh_post(url, {"ref":branch,"inputs":inputs}, token)

def fetch_results(owner, repo, wf_file, branch, client_run_id, token):
    r=list_workflow_runs(owner,repo,wf_file,branch,token,per_page=50)
    if not r.ok: return None,f"List runs failed {r.status_code}"
    runs=r.json().get("workflow_runs",[])
    run=find_run_by_tag(runs,client_run_id)
    if not run: return None,"No run found"
    arts=get_run_artifacts(owner,repo,run["id"],token)
    if not arts.ok: return {"run":run,"artifacts":[]},f"Artifacts failed {arts.status_code}"
    return {"run":run,"artifacts":arts.json().get("artifacts",[])},None

def download_artifact(owner, repo, art_id, token): return download_artifact_zip(owner,repo,art_id,token)
