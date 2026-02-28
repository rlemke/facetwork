/* Per-page list filter — shared across all list views.
 *
 * Usage: Add an input with data-list-filter="<selector>" where <selector>
 * matches the elements to show/hide.  The filter matches against the
 * element's textContent (case-insensitive).
 *
 * For <details> elements the filter also shows/hides the entire group and
 * auto-opens groups with matches.
 */
(function() {
    function initFilters() {
        var inputs = document.querySelectorAll("[data-list-filter]");
        for (var i = 0; i < inputs.length; i++) {
            (function(input) {
                var selector = input.getAttribute("data-list-filter");
                input.addEventListener("input", function() {
                    var q = input.value.toLowerCase().trim();
                    var items = document.querySelectorAll(selector);
                    for (var j = 0; j < items.length; j++) {
                        var item = items[j];
                        if (!q) {
                            item.style.display = "";
                            continue;
                        }
                        var text = item.textContent.toLowerCase();
                        item.style.display = text.indexOf(q) >= 0 ? "" : "none";
                    }
                    // For ns-group details: hide groups with zero visible children
                    var groups = document.querySelectorAll("details.ns-group");
                    for (var k = 0; k < groups.length; k++) {
                        var group = groups[k];
                        var rows = group.querySelectorAll(selector);
                        var visible = 0;
                        for (var m = 0; m < rows.length; m++) {
                            if (rows[m].style.display !== "none") visible++;
                        }
                        group.style.display = q && visible === 0 ? "none" : "";
                        if (q && visible > 0) group.open = true;
                    }
                });
            })(inputs[i]);
        }
    }

    // Run on DOMContentLoaded and after HTMX swaps
    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", initFilters);
    } else {
        initFilters();
    }
    document.body.addEventListener("htmx:afterSwap", initFilters);
})();
