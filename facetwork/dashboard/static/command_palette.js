/* Command palette — Cmd+K / Ctrl+K global search */

export function initCommandPalette() {
    var overlay = document.getElementById("cmd-palette");
    var input = document.getElementById("cmd-palette-input");
    if (!overlay || !input) return;

    function open() {
        overlay.style.display = "";
        input.value = "";
        input.focus();
        var results = document.getElementById("cmd-palette-results");
        if (results) results.innerHTML = '<div class="cmd-palette-empty">Type to search across all resources</div>';
    }

    function close() {
        overlay.style.display = "none";
        input.value = "";
    }

    // Cmd+K / Ctrl+K to open
    document.addEventListener("keydown", function(e) {
        if ((e.metaKey || e.ctrlKey) && e.key === "k") {
            e.preventDefault();
            if (overlay.style.display === "none") {
                open();
            } else {
                close();
            }
        }
        if (e.key === "Escape" && overlay.style.display !== "none") {
            close();
        }
    });

    // Click overlay backdrop to close
    overlay.addEventListener("click", function(e) {
        if (e.target === overlay) close();
    });

    // Keyboard navigation in results
    input.addEventListener("keydown", function(e) {
        var results = document.getElementById("cmd-palette-results");
        var items = results ? results.querySelectorAll(".cmd-palette-item") : [];
        if (items.length === 0) return;

        var active = results.querySelector(".cmd-palette-item.active");
        var idx = -1;
        for (var i = 0; i < items.length; i++) {
            if (items[i] === active) { idx = i; break; }
        }

        if (e.key === "ArrowDown") {
            e.preventDefault();
            if (active) active.classList.remove("active");
            idx = (idx + 1) % items.length;
            items[idx].classList.add("active");
            items[idx].scrollIntoView({block: "nearest"});
        } else if (e.key === "ArrowUp") {
            e.preventDefault();
            if (active) active.classList.remove("active");
            idx = idx <= 0 ? items.length - 1 : idx - 1;
            items[idx].classList.add("active");
            items[idx].scrollIntoView({block: "nearest"});
        } else if (e.key === "Enter") {
            var href = active && active.getAttribute("data-href");
            // Same-site paths only: a result's href must never be able to run
            // script (javascript:) or leave the dashboard (//host, https://).
            if (href && href.charAt(0) === "/" && href.charAt(1) !== "/" && href.charAt(1) !== "\\") {
                window.location.href = href;
                close();
            }
        }
    });
}
