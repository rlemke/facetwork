/**
 * View toggle — switch between flat/tree/graph/timeline step views.
 *
 * Usage:
 *   <button class="view-toggle-btn" data-view="flat" onclick="switchView('flat', this)">Flat</button>
 *   <div id="v2-step-flat">...</div>
 */

export function switchView(name, btn) {
    var views = ['tasks', 'flat', 'tree', 'graph', 'timeline'];
    for (var i = 0; i < views.length; i++) {
        var el = document.getElementById('v2-step-' + views[i]);
        if (el) el.style.display = (views[i] === name) ? '' : 'none';
    }
    var btns = document.querySelectorAll('.view-toggle-btn');
    for (var j = 0; j < btns.length; j++) {
        btns[j].classList.toggle('active', btns[j] === btn);
    }
}

export function initViewToggle() {
    // Make switchView available globally for onclick handlers
    window.switchView = switchView;
}
