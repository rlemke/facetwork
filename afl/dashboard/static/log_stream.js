/**
 * Log streaming client using Server-Sent Events.
 *
 * Usage:
 *   <button data-log-stream="/api/steps/STEP_ID/logs/stream"
 *           data-log-target="#step-log-body">Live</button>
 */

var source = null;

export function connect(url, targetSelector) {
    disconnect();
    var target = document.querySelector(targetSelector);
    if (!target) return;

    source = new EventSource(url);
    source.onmessage = function (event) {
        try {
            var log = JSON.parse(event.data);
            var row = document.createElement("tr");

            var timeCell = document.createElement("td");
            var timeEl = document.createElement("time");
            timeEl.setAttribute("data-ts", log.time);
            timeEl.textContent = new Date(log.time).toLocaleTimeString();
            timeCell.appendChild(timeEl);
            row.appendChild(timeCell);

            var sourceCell = document.createElement("td");
            sourceCell.textContent = log.source;
            row.appendChild(sourceCell);

            var levelCell = document.createElement("td");
            var badge = document.createElement("span");
            var colorMap = { info: "primary", warning: "warning", error: "danger", success: "success" };
            badge.className = "badge badge-" + (colorMap[log.level] || "secondary");
            badge.textContent = log.level;
            levelCell.appendChild(badge);
            row.appendChild(levelCell);

            var msgCell = document.createElement("td");
            msgCell.textContent = log.message;
            row.appendChild(msgCell);

            target.appendChild(row);
        } catch (e) {
            /* ignore parse errors */
        }
    };

    source.onerror = function () {
        /* auto-reconnect handled by EventSource */
    };
}

export function disconnect() {
    if (source) {
        source.close();
        source = null;
    }
}

export function initLogStream() {
    document.addEventListener("click", function (e) {
        var btn = e.target.closest("[data-log-stream]");
        if (!btn) return;

        btn.classList.toggle("active");
        if (btn.classList.contains("active")) {
            connect(btn.getAttribute("data-log-stream"), btn.getAttribute("data-log-target"));
            btn.textContent = "Stop";
        } else {
            disconnect();
            btn.textContent = "Live";
        }
    });
}
