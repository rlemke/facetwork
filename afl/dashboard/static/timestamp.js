/* timestamp.js — format UTC <time> elements to local timezone */

export function formatTimestamps(selector) {
    if (selector === undefined) selector = "time[data-ts]";
    var els = document.querySelectorAll(selector);
    for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var ms = parseInt(el.getAttribute("data-ts"), 10);
        if (isNaN(ms)) continue;
        var d = new Date(ms);
        var pad = function(n) { return n < 10 ? "0" + n : n; };
        el.textContent = d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
            pad(d.getDate()) + " " + pad(d.getHours()) + ":" +
            pad(d.getMinutes()) + ":" + pad(d.getSeconds());
    }
}
