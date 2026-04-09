/* Per-page list filter — shared across all list views.
 *
 * Usage: Add an input with data-list-filter="<selector>" where <selector>
 * matches the elements to show/hide.  The filter matches against the
 * element's textContent (case-insensitive).
 *
 * For <details> elements the filter also shows/hides the entire group and
 * auto-opens groups with matches.
 *
 * Also preserves <details class="ns-group"> open/closed state across
 * HTMX auto-refresh swaps so that browsing an expanded accordion doesn't
 * collapse when the page refreshes.
 */

/* Track which ns-group accordions are open by namespace text */
var _nsGroupOpenSet = {};
var _nsGroupStateInitialized = false;

function _initNsGroupPreservation() {
    if (_nsGroupStateInitialized) return;
    _nsGroupStateInitialized = true;

    document.body.addEventListener("htmx:beforeSwap", function(evt) {
        /* Only capture state for containers that hold ns-group elements */
        var target = evt.detail.target;
        if (!target) return;
        var groups = target.querySelectorAll("details.ns-group");
        if (groups.length === 0) return;

        _nsGroupOpenSet = {};
        for (var i = 0; i < groups.length; i++) {
            var key = _nsGroupKey(groups[i]);
            if (key) {
                _nsGroupOpenSet[key] = groups[i].open;
            }
        }
    });

    document.body.addEventListener("htmx:afterSwap", function(evt) {
        var target = evt.detail.target;
        if (!target) return;
        var groups = target.querySelectorAll("details.ns-group");
        if (groups.length === 0) return;

        for (var i = 0; i < groups.length; i++) {
            var key = _nsGroupKey(groups[i]);
            if (key && key in _nsGroupOpenSet) {
                groups[i].open = _nsGroupOpenSet[key];
            }
        }
    });
}

function _nsGroupKey(details) {
    /* Use the namespace text from <summary><strong> as a stable key */
    var strong = details.querySelector("summary strong");
    return strong ? strong.textContent.trim() : null;
}

export function initFilters() {
    _initNsGroupPreservation();

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
