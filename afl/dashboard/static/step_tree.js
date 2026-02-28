/* Step tree interaction — expand/collapse all, search filter, keyboard nav */
(function() {
    function initStepTree() {
        var container = document.getElementById("v2-step-tree-content");
        if (!container) return;

        // Expand all
        var expandBtn = document.getElementById("step-tree-expand-all");
        if (expandBtn) {
            expandBtn.addEventListener("click", function() {
                container.querySelectorAll("details.step-tree-node").forEach(function(d) {
                    d.open = true;
                });
            });
        }

        // Collapse all
        var collapseBtn = document.getElementById("step-tree-collapse-all");
        if (collapseBtn) {
            collapseBtn.addEventListener("click", function() {
                container.querySelectorAll("details.step-tree-node").forEach(function(d) {
                    d.open = false;
                });
            });
        }

        // Search within tree
        var searchInput = document.getElementById("step-tree-search");
        if (searchInput) {
            searchInput.addEventListener("input", function() {
                var q = searchInput.value.toLowerCase().trim();
                var nodes = container.querySelectorAll("details.step-tree-node");
                nodes.forEach(function(node) {
                    var summary = node.querySelector("summary");
                    if (!summary) return;
                    var text = summary.textContent.toLowerCase();
                    if (!q) {
                        node.style.display = "";
                        // Remove any highlight
                        summary.querySelectorAll(".search-highlight").forEach(function(hl) {
                            hl.outerHTML = hl.textContent;
                        });
                        return;
                    }
                    if (text.indexOf(q) >= 0) {
                        node.style.display = "";
                        node.open = true;
                        // Open all parents
                        var parent = node.parentElement;
                        while (parent) {
                            if (parent.tagName === "DETAILS") parent.open = true;
                            parent = parent.parentElement;
                        }
                    } else {
                        node.style.display = "none";
                    }
                });
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initStepTree);
    } else {
        initStepTree();
    }
    document.body.addEventListener("htmx:afterSwap", initStepTree);
})();
