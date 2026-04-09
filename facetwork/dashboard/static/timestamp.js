/* timestamp.js — format UTC <time> elements to local timezone */

function _pad(n) { return n < 10 ? "0" + n : n; }

export function formatTimestamps(selector) {
    if (selector === undefined) selector = "time[data-ts]";
    var els = document.querySelectorAll(selector);
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var ms = parseInt(el.getAttribute("data-ts"), 10);
        if (isNaN(ms)) continue;
        var d = new Date(ms);
        el.textContent = d.getFullYear() + "-" + _pad(d.getMonth() + 1) + "-" +
            _pad(d.getDate()) + " " + _pad(d.getHours()) + ":" +
            _pad(d.getMinutes()) + ":" + _pad(d.getSeconds());
    }
    // Split timestamps: time on top, date on bottom
    var splits = document.querySelectorAll(".ts-split[data-ts]");
    for (var j = 0; j < splits.length; j++) {
        var sp = splits[j];
        var tsMs = parseInt(sp.getAttribute("data-ts"), 10);
        if (isNaN(tsMs)) continue;
        var dt = new Date(tsMs);
        var timePart = _pad(dt.getHours()) + ":" + _pad(dt.getMinutes()) + ":" + _pad(dt.getSeconds());
        var datePart = dt.getFullYear() + "-" + _pad(dt.getMonth() + 1) + "-" + _pad(dt.getDate());
        sp.innerHTML = timePart + '<br><small class="secondary">' + datePart + '</small>';
    }
}
