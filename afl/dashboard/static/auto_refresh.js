/* auto_refresh.js — HTMX auto-refresh helper */

export function setupAutoRefresh(selector, url, intervalMs) {
    if (intervalMs === undefined) intervalMs = 5000;
    var el = document.querySelector(selector);
    if (!el) return null;
    el.setAttribute("hx-get", url);
    el.setAttribute("hx-trigger", "every " + intervalMs + "ms");
    el.setAttribute("hx-swap", "innerHTML");
    if (typeof htmx !== "undefined") {
        htmx.process(el);
    }
    return el;
}
