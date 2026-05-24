/**
 * UI-Enhancements v1.0 — JavaScript-Layer
 * Erstellt: 2026-05-16
 *
 * Stellt globale Helper bereit:
 *   - window.toast({type, message, duration}) — Toast-Notification
 *   - window.toastSuccess / .toastError / .toastWarning / .toastInfo (shortcuts)
 *   - HTMX-Event-Listener fuer automatische Toasts bei Server-Actions
 *
 * Nicht-destruktiv: ohne diese Datei laeuft das Dashboard wie vorher.
 * Sicherheits-Note: nutzt durchgaengig textContent (kein innerHTML) zum
 *                   Schutz gegen XSS bei user-supplied messages.
 */
(function() {
    'use strict';

    const TOAST_DURATION_DEFAULT = 4000;
    const TOAST_CONTAINER_ID = 'toast-container';

    // ------------------------------------------------------------
    // Container-Setup
    // ------------------------------------------------------------
    function ensureContainer() {
        let container = document.getElementById(TOAST_CONTAINER_ID);
        if (!container) {
            container = document.createElement('div');
            container.id = TOAST_CONTAINER_ID;
            container.className = 'toast-container';
            container.setAttribute('aria-live', 'polite');
            container.setAttribute('aria-atomic', 'true');
            document.body.appendChild(container);
        }
        return container;
    }

    // ------------------------------------------------------------
    // Lucide-Icon-Map fuer Toast-Types (falls Lucide geladen)
    // ------------------------------------------------------------
    const TYPE_ICONS = {
        success: 'check-circle',
        warning: 'alert-triangle',
        danger:  'x-circle',
        error:   'x-circle',
        info:    'info'
    };

    function makeIcon(type) {
        const iconName = TYPE_ICONS[type] || 'bell';
        const wrap = document.createElement('span');
        wrap.className = 'toast__icon';
        const i = document.createElement('i');
        i.setAttribute('data-lucide', iconName);
        i.className = 'icon';
        wrap.appendChild(i);
        return wrap;
    }

    // ------------------------------------------------------------
    // Toast erstellen — durchgaengig textContent, kein innerHTML
    // ------------------------------------------------------------
    window.toast = function(opts) {
        const type = opts.type || 'info';
        const msg = opts.message || '';
        const duration = opts.duration !== undefined ? opts.duration : TOAST_DURATION_DEFAULT;
        const persistent = opts.persistent === true;

        const container = ensureContainer();

        const toast = document.createElement('div');
        toast.className = 'toast toast--' + (type === 'error' ? 'danger' : type);
        toast.setAttribute('role', 'status');

        // Icon (falls Lucide vorhanden)
        if (window.lucide) {
            toast.appendChild(makeIcon(type));
        }

        // Message — textContent garantiert keinen HTML-Inject
        const msgEl = document.createElement('span');
        msgEl.className = 'toast__msg';
        msgEl.textContent = msg;
        toast.appendChild(msgEl);

        // Close-Button (Unicode "x" als textContent, kein innerHTML)
        const close = document.createElement('button');
        close.className = 'toast__close';
        close.setAttribute('aria-label', 'Schliessen');
        close.textContent = '×';  // U+00D7 multiplication sign (visuell wie &times;)
        close.addEventListener('click', function() { removeToast(toast); });
        toast.appendChild(close);

        container.appendChild(toast);

        // Lucide-Icons in dem neuen Toast rendern
        if (window.lucide && window.lucide.createIcons) {
            window.lucide.createIcons({ nameAttr: 'data-lucide' });
        }

        // Auto-Remove
        if (!persistent && duration > 0) {
            setTimeout(function() { removeToast(toast); }, duration);
        }

        return toast;
    };

    function removeToast(toast) {
        if (!toast || !toast.parentNode) return;
        toast.classList.add('toast--leaving');
        setTimeout(function() {
            if (toast.parentNode) toast.parentNode.removeChild(toast);
        }, 200);
    }

    // Shortcuts
    window.toastSuccess = function(msg, duration) { return window.toast({type:'success', message:msg, duration:duration}); };
    window.toastError   = function(msg, duration) { return window.toast({type:'danger',  message:msg, duration:duration}); };
    window.toastWarning = function(msg, duration) { return window.toast({type:'warning', message:msg, duration:duration}); };
    window.toastInfo    = function(msg, duration) { return window.toast({type:'info',    message:msg, duration:duration}); };

    // ------------------------------------------------------------
    // HTMX-Event-Listener: automatische Toasts bei Server-Actions
    // ------------------------------------------------------------
    document.addEventListener('htmx:responseError', function(e) {
        const status = e.detail && e.detail.xhr ? e.detail.xhr.status : '?';
        window.toastError('Server-Fehler: ' + status);
    });

    document.addEventListener('htmx:sendError', function() {
        window.toastError('Netzwerk-Fehler');
    });

    document.addEventListener('htmx:timeout', function() {
        window.toastWarning('Request-Timeout');
    });

    // Custom-Trigger via HX-Trigger-Header
    // Server kann HX-Trigger: {"toast":{"type":"success","message":"..."}} senden
    document.body.addEventListener('toast', function(e) {
        if (e.detail) window.toast(e.detail);
    });

    // ------------------------------------------------------------
    // Lucide-Icons auto-rendern wenn vorhanden
    // ------------------------------------------------------------
    document.addEventListener('DOMContentLoaded', function() {
        if (window.lucide && window.lucide.createIcons) {
            window.lucide.createIcons({ nameAttr: 'data-lucide' });
        }
    });

    // Nach jedem HTMX-Swap: Lucide-Icons in neu eingefuegtem HTML rendern
    document.addEventListener('htmx:afterSwap', function() {
        if (window.lucide && window.lucide.createIcons) {
            window.lucide.createIcons({ nameAttr: 'data-lucide' });
        }
    });
})();
