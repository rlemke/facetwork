// Workflow list actions: per-row delete + multi-select bulk delete.
//
// Uses event delegation on document so it keeps working after HTMX swaps the
// #runner-groups partial (auto-refresh / Refresh button) without re-binding.

function bulkBar() {
    return document.getElementById("bulk-actions");
}

function syncBulkBar() {
    const checked = document.querySelectorAll("[data-runner-checkbox]:checked");
    const countEl = document.getElementById("selected-count");
    if (countEl) {
        countEl.textContent = `${checked.length} selected`;
    }
    const delBtn = document.getElementById("delete-selected");
    if (delBtn) {
        delBtn.disabled = checked.length === 0;
    }
    // Keep "select all" in sync with the row checkboxes.
    const all = document.querySelectorAll("[data-runner-checkbox]");
    const selectAll = document.getElementById("select-all-runners");
    if (selectAll) {
        selectAll.checked = all.length > 0 && checked.length === all.length;
        selectAll.indeterminate = checked.length > 0 && checked.length < all.length;
    }
}

function enterSelectMode() {
    document.body.classList.add("wf-select-mode");
    const toggle = document.getElementById("select-toggle");
    if (toggle) toggle.hidden = true;
    const bar = bulkBar();
    if (bar) bar.hidden = false;
    syncBulkBar();
}

function exitSelectMode() {
    document.body.classList.remove("wf-select-mode");
    const toggle = document.getElementById("select-toggle");
    if (toggle) toggle.hidden = false;
    const bar = bulkBar();
    if (bar) bar.hidden = true;
    document
        .querySelectorAll("[data-runner-checkbox]")
        .forEach((cb) => {
            cb.checked = false;
        });
    const selectAll = document.getElementById("select-all-runners");
    if (selectAll) {
        selectAll.checked = false;
        selectAll.indeterminate = false;
    }
}

async function postDelete(url, body) {
    const opts = { method: "POST" };
    if (body !== undefined) {
        opts.headers = { "Content-Type": "application/json" };
        opts.body = JSON.stringify(body);
    }
    const resp = await fetch(url, opts);
    let data = {};
    try {
        data = await resp.json();
    } catch (e) {
        /* ignore */
    }
    if (!resp.ok || !data.success) {
        throw new Error(data.error || `Request failed (${resp.status})`);
    }
    return data;
}

async function deleteOne(btn) {
    const id = btn.getAttribute("data-delete-runner");
    const name = btn.getAttribute("data-runner-name") || id;
    if (!window.confirm(`Delete workflow run "${name}"?\n\nThis permanently removes its steps, tasks, and logs.`)) {
        return;
    }
    btn.disabled = true;
    try {
        await postDelete(`/api/runners/${id}/delete`);
        window.location.reload();
    } catch (err) {
        btn.disabled = false;
        window.alert(`Delete failed: ${err.message}`);
    }
}

async function deleteSelected() {
    const checked = Array.from(
        document.querySelectorAll("[data-runner-checkbox]:checked"),
    );
    const ids = checked.map((cb) => cb.value);
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} workflow run(s)?\n\nThis permanently removes their steps, tasks, and logs.`)) {
        return;
    }
    const delBtn = document.getElementById("delete-selected");
    if (delBtn) delBtn.disabled = true;
    try {
        await postDelete("/api/runners/bulk-delete", { runner_ids: ids });
        window.location.reload();
    } catch (err) {
        if (delBtn) delBtn.disabled = false;
        window.alert(`Bulk delete failed: ${err.message}`);
    }
}

export function initWorkflowActions() {
    // Re-sync the bar after HTMX swaps (row checkboxes are recreated).
    syncBulkBar();

    if (document.body.dataset.wfActionsBound === "1") return;
    document.body.dataset.wfActionsBound = "1";

    document.addEventListener("click", (e) => {
        if (e.target.closest("#select-toggle")) {
            enterSelectMode();
        } else if (e.target.closest("#cancel-select")) {
            exitSelectMode();
        } else if (e.target.closest("#delete-selected")) {
            deleteSelected();
        } else {
            const delBtn = e.target.closest("[data-delete-runner]");
            if (delBtn) {
                e.preventDefault();
                deleteOne(delBtn);
            }
        }
    });

    document.addEventListener("change", (e) => {
        if (e.target.id === "select-all-runners") {
            const checked = e.target.checked;
            document
                .querySelectorAll("[data-runner-checkbox]")
                .forEach((cb) => {
                    cb.checked = checked;
                });
            syncBulkBar();
        } else if (e.target.matches("[data-select-all-group]")) {
            // Select/deselect every row within this group's table.
            const checked = e.target.checked;
            const table = e.target.closest("table");
            if (table) {
                table
                    .querySelectorAll("[data-runner-checkbox]")
                    .forEach((cb) => {
                        cb.checked = checked;
                    });
            }
            syncBulkBar();
        } else if (e.target.matches("[data-runner-checkbox]")) {
            syncBulkBar();
        }
    });
}
