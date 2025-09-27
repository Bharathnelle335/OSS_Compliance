import os
import json
from nicegui import ui

from tools import scanoss
from tools.github_api import (
    new_client_tag,
    fetch_all_tags,
    fetch_all_branches,
    parse_owner_repo_any,
    normalize_github_url_and_ref,
    list_workflow_runs,
    find_run_by_tag,
    get_run_artifacts,
    download_artifact_zip,
)

TOKEN = os.getenv("GITHUB_TOKEN", "")

ui.page_title("OSS Compliance")

# ---------------- Header ----------------
with ui.header().classes('items-center justify-between px-5 py-3 shadow-md'):
    ui.label("OSS Compliance").classes("text-2xl font-bold")
    ui.label("© EY Internal Use only").classes("text-sm text-gray-500")

# ---------------- Settings ----------------
with ui.card().classes('w-full max-w-screen-xl mx-auto mt-4'):
    ui.label("Settings").classes("text-lg font-semibold")
    with ui.row().classes('gap-4 items-end'):
        token_in = ui.input("GitHub Token (PAT)", value=TOKEN, password=True).classes('w-[28rem]')
        ui.label("Scopes: repo, workflow").classes("text-sm text-gray-600")

# ---------------- SCANOSS Panel ----------------
with ui.card().classes('w-full max-w-screen-xl mx-auto mt-4'):
    ui.label("SCANOSS").classes("text-lg font-semibold")

    with ui.row().classes('gap-4'):
        sc_owner  = ui.input('Owner/Org', value=scanoss.DEFAULTS['owner']).classes('w-64')
        sc_repo   = ui.input('Repository', value=scanoss.DEFAULTS['repo']).classes('w-72')
        sc_wf     = ui.input('Workflow file', value=scanoss.DEFAULTS['workflow_file']).classes('w-72')
        sc_branch = ui.input('Workflow branch', value=scanoss.DEFAULTS['branch']).classes('w-40')

    with ui.row().classes('gap-4 mt-2'):
        scan_type      = ui.select(['docker','git','upload-zip','upload-tar'], value='docker', label='scan_type').classes('w-48')
        image_mode     = ui.select(['manual','syft'], value='manual', label='image_scan_mode').classes('w-40')
        enable_scanoss = ui.switch('enable_scanoss', value=True)
        client_run_id  = ui.input('client_run_id', value=new_client_tag()).classes('w-64')

    with ui.row().classes('gap-4 mt-2'):
        source_input = ui.input('Source', value='alpine:latest', placeholder='image ref / repo URL / archive URL').classes('w-[40rem]')
        git_ref_input = ui.input('Git ref (for scan_type=git)', value='').classes('w-60')

    # Ref picker (for git)
    with ui.expansion('Pick ref (tags / branches)', icon='search').classes('mt-2 w-full'):
        ref_info = ui.label('').classes('text-sm text-gray-600')
        with ui.row().classes('gap-3'):
            load_refs_btn = ui.button('Load refs')
            tag_sel    = ui.select(['-- choose --'], value='-- choose --', label='Tag').classes('w-64')
            branch_sel = ui.select(['-- choose --'], value='-- choose --', label='Branch').classes('w-64')

        def do_load_refs():
            owner, repo = parse_owner_repo_any(source_input.value)
            if not (owner and repo):
                norm, _, _ = normalize_github_url_and_ref(source_input.value, '')
                owner, repo = parse_owner_repo_any(norm)
            if not (owner and repo):
                ui.notify('Enter a valid GitHub repo URL or owner/repo to load refs', type='warning')
                return
            tags, terr = fetch_all_tags(owner, repo, token_in.value)
            branches, berr = fetch_all_branches(owner, repo, token_in.value)
            tag_sel.options = ['-- choose --'] + (tags or [])
            branch_sel.options = ['-- choose --'] + (branches or [])
            msg = []
            if terr: msg.append(f'Tags: {terr}')
            if berr: msg.append(f'Branches: {berr}')
            ref_info.text = ' | '.join(msg) if msg else f'Loaded {len(tags)} tags, {len(branches)} branches'

        load_refs_btn.on('click', do_load_refs)

    preview = ui.textarea('Payload preview').classes('w-full h-40 mt-2')

    def _build_inputs_for_preview():
        chosen_ref = None
        if tag_sel.value and tag_sel.value != '-- choose --':
            chosen_ref = tag_sel.value
        elif branch_sel.value and branch_sel.value != '-- choose --':
            chosen_ref = branch_sel.value
        return scanoss.build_inputs(
            scan_type=scan_type.value,
            source=source_input.value.strip(),
            image_scan_mode=image_mode.value,
            git_ref=chosen_ref or git_ref_input.value.strip(),
            enable_scanoss=enable_scanoss.value,
            client_run_id=client_run_id.value.strip() or new_client_tag(),
        )

    def do_preview():
        inputs = _build_inputs_for_preview()
        payload = {'ref': sc_branch.value.strip() or 'main', 'inputs': inputs}
        preview.value = json.dumps(payload, indent=2)

    def do_run():
        stype = scan_type.value
        src = source_input.value.strip()
        if stype == 'docker' and not src:
            ui.notify('docker_image is required for scan_type=docker', type='negative'); return
        if stype == 'git' and not src:
            ui.notify('git_url is required for scan_type=git', type='negative'); return
        if stype in ('upload-zip','upload-tar') and not src:
            ui.notify(f'archive_url is required for scan_type={stype}', type='negative'); return

        inputs = _build_inputs_for_preview()
        r = scanoss.trigger(sc_owner.value, sc_repo.value, sc_wf.value, sc_branch.value, inputs, token_in.value)
        if r.status_code == 204:
            ui.notify('SCANOSS scan started', type='positive'); do_preview()
        else:
            ui.notify(f'Failed: {r.status_code}', type='negative')
            preview.value = (r.text or '')[:1000]

    with ui.row().classes('gap-3 mt-1'):
        ui.button('Preview Payload', on_click=do_preview, icon='preview')
        ui.button('Run SCANOSS', on_click=do_run, icon='play_arrow').props('unelevated')

    # ---------------- Results ----------------
    ui.separator()
    ui.label('Results').classes('text-base font-semibold mt-2')
    with ui.row().classes('gap-3'):
        res_tag = ui.input('Run tag (client_run_id)', value=new_client_tag()).classes('w-80')
        ui.button('Use current run id',
                  on_click=lambda: setattr(res_tag, 'value', client_run_id.value),
                  icon='content_paste')
        check_btn = ui.button('Check status & fetch', icon='search')

    status_area = ui.markdown('').classes('text-sm')
    artifact_info = ui.markdown('')

    def do_check():
        tag = res_tag.value.strip()
        if not tag:
            ui.notify('Provide a run tag (client_run_id)', type='warning'); return
        runs_resp = list_workflow_runs(
            scanoss.DEFAULTS["owner"],
            scanoss.DEFAULTS["repo"],
            scanoss.DEFAULTS["workflow_file"],
            scanoss.DEFAULTS["branch"],
            token_in.value,
            per_page=50,
        )
        if not runs_resp.ok:
            status_area.set_text(f"**Error**: list runs → {runs_resp.status_code} {runs_resp.text[:400]}"); return

        runs = runs_resp.json().get('workflow_runs', [])
        run = find_run_by_tag(runs, tag)
        if not run:
            status_area.set_text('No run found yet for this tag. Try again in a bit.'); return

        run_id = run['id']
        status = run.get('status')
        concl  = run.get('conclusion')
        html_url = run.get('html_url')
        started = run.get('run_started_at')
        status_area.set_text(
            f"**Run:** [{run_id}]({html_url})\n\n"
            f"**Status:** {status} | **Conclusion:** {concl or '—'} | **Started:** {started or '—'}"
        )

        if status != 'completed':
            ui.notify('Still running (queued/in_progress)', type='warning'); return

        arts_resp = get_run_artifacts(sc_owner.value, sc_repo.value, run_id, token_in.value)
        if not arts_resp.ok:
            artifact_info.set_text(f"Artifacts error: {arts_resp.status_code} {arts_resp.text[:400]}"); return

        artifacts = arts_resp.json().get('artifacts', [])
        if not artifacts:
            artifact_info.set_text('No artifacts found for this run.'); return

        art = next((a for a in artifacts if tag in (a.get('name') or '')), artifacts[0])
        artifact_info.set_text(f"**Artifact:** `{art.get('name')}` • size ≈ {art.get('size_in_bytes', 0)} bytes")

        data = download_artifact_zip(sc_owner.value, sc_repo.value, art['id'], token_in.value)
        if not data:
            ui.notify('Failed to download artifact zip (empty response).', type='negative'); return

        ui.download(data, f"{art.get('name','scanoss-results')}.zip")

    check_btn.on('click', do_check)

# ---------------- Footer ----------------
with ui.footer().classes('justify-center items-center py-3 text-gray-600 mt-6'):
    ui.label('© EY Internal Use only')

ui.run()
