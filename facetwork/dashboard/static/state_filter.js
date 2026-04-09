/**
 * State filter for step views — filter rows by state category and maintain
 * tree view state across HTMX refreshes.
 *
 * Expects:
 *   - Elements with [data-state-category] attribute on rows
 *   - #state-tabs container with [data-filter] buttons
 *   - #step-tree-content for tree view state preservation
 */

export function initStateFilter() {
    var stateTabs = document.getElementById("state-tabs");
    if (!stateTabs) return;

    var currentFilter = "all";

    function applyStateFilter(filter) {
        currentFilter = filter;
        var elements = document.querySelectorAll("[data-state-category]");
        for (var i = 0; i < elements.length; i++) {
            var el = elements[i];
            if (filter === "all" || el.getAttribute("data-state-category") === filter) {
                el.style.display = "";
            } else {
                el.style.display = "none";
            }
        }
        var buttons = stateTabs.querySelectorAll(".view-toggle-btn");
        for (var j = 0; j < buttons.length; j++) {
            if (buttons[j].getAttribute("data-filter") === filter) {
                buttons[j].classList.add("active");
            } else {
                buttons[j].classList.remove("active");
            }
        }
    }

    function updateStateCounts() {
        var elements = document.querySelectorAll("[data-state-category]");
        var counts = {all: 0, running: 0, complete: 0, error: 0, other: 0};
        for (var i = 0; i < elements.length; i++) {
            var cat = elements[i].getAttribute("data-state-category");
            counts.all++;
            if (counts.hasOwnProperty(cat)) {
                counts[cat]++;
            }
        }
        for (var key in counts) {
            var span = document.getElementById("count-" + key);
            if (span) {
                span.textContent = "(" + counts[key] + ")";
            }
        }
    }

    stateTabs.addEventListener("click", function(e) {
        var btn = e.target.closest("[data-filter]");
        if (btn) {
            applyStateFilter(btn.getAttribute("data-filter"));
            updateStateCounts();
        }
    });

    document.body.addEventListener("htmx:afterSwap", function() {
        applyStateFilter(currentFilter);
        updateStateCounts();
    });

    // Tree view state preservation across refresh
    var treeOpenSet = {};
    var treeScrollTop = 0;
    var treeEl = document.getElementById("step-tree-content");
    if (treeEl) {
        treeEl.addEventListener("htmx:beforeSwap", function() {
            treeOpenSet = {};
            treeScrollTop = treeEl.scrollTop;
            var nodes = treeEl.querySelectorAll("details.step-tree-node");
            for (var i = 0; i < nodes.length; i++) {
                var link = nodes[i].querySelector("summary a[href]");
                if (link && nodes[i].open) {
                    treeOpenSet[link.getAttribute("href")] = true;
                }
            }
        });
        treeEl.addEventListener("htmx:afterSwap", function() {
            var nodes = treeEl.querySelectorAll("details.step-tree-node");
            for (var i = 0; i < nodes.length; i++) {
                var link = nodes[i].querySelector("summary a[href]");
                if (link) {
                    var href = link.getAttribute("href");
                    if (treeOpenSet[href]) {
                        nodes[i].open = true;
                    } else if (!(href in treeOpenSet)) {
                        /* new node - keep template default */
                    } else {
                        nodes[i].open = false;
                    }
                }
            }
            treeEl.scrollTop = treeScrollTop;
        });
    }

    updateStateCounts();
}
