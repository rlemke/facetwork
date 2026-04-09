/* state_utils.js — centralized state color and label logic */

export var STATE_COLORS = {
    running: "var(--state-running)",
    completed: "var(--state-completed)",
    failed: "var(--state-failed)",
    warning: "var(--state-warning)",
    pending: "var(--state-pending)",
    paused: "var(--state-paused)"
};

export function stateLabel(dotted) {
    if (!dotted) return "unknown";
    return dotted.split(".").pop();
}

export function stateColor(state) {
    var key = stateLabel(state).toLowerCase();
    return STATE_COLORS[key] || "var(--state-pending)";
}
