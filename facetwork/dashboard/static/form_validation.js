/**
 * Form validation module — adds inline validation feedback and JSON syntax checking.
 */

export function initFormValidation() {
    // Add validation to all forms with data-validate attribute
    document.querySelectorAll('form[data-validate]').forEach(form => {
        form.addEventListener('submit', function(e) {
            if (!validateForm(form)) {
                e.preventDefault();
            }
        });

        // Live validation on blur
        form.querySelectorAll('input[required], textarea[required]').forEach(input => {
            input.addEventListener('blur', () => validateField(input));
        });
    });

    // JSON validation for textareas with data-json attribute
    document.querySelectorAll('textarea[data-json]').forEach(textarea => {
        textarea.addEventListener('blur', () => validateJson(textarea));
        textarea.addEventListener('input', () => clearValidation(textarea));
    });
}

function validateForm(form) {
    let valid = true;
    form.querySelectorAll('input[required], textarea[required]').forEach(input => {
        if (!validateField(input)) valid = false;
    });
    form.querySelectorAll('textarea[data-json]').forEach(textarea => {
        if (!validateJson(textarea)) valid = false;
    });
    return valid;
}

function validateField(input) {
    clearValidation(input);
    if (input.required && !input.value.trim()) {
        showError(input, 'This field is required');
        return false;
    }
    if (input.pattern && input.value) {
        const re = new RegExp('^' + input.pattern + '$');
        if (!re.test(input.value)) {
            showError(input, input.title || 'Invalid format');
            return false;
        }
    }
    return true;
}

function validateJson(textarea) {
    clearValidation(textarea);
    const val = textarea.value.trim();
    if (!val) return true;
    try {
        JSON.parse(val);
        return true;
    } catch (e) {
        showError(textarea, 'Invalid JSON: ' + e.message);
        return false;
    }
}

function showError(el, message) {
    el.classList.add('validation-error');
    el.setAttribute('aria-invalid', 'true');
    let msg = el.nextElementSibling;
    if (!msg || !msg.classList.contains('validation-message')) {
        msg = document.createElement('small');
        msg.className = 'validation-message';
        el.parentNode.insertBefore(msg, el.nextSibling);
    }
    msg.textContent = message;
}

function clearValidation(el) {
    el.classList.remove('validation-error');
    el.removeAttribute('aria-invalid');
    const msg = el.nextElementSibling;
    if (msg && msg.classList.contains('validation-message')) {
        msg.remove();
    }
}

// Auto-init on DOMContentLoaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initFormValidation);
} else {
    initFormValidation();
}
