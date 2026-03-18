// Restore saved theme before CSS renders — prevents flash of wrong theme.
// This file is loaded synchronously (no defer/async) before the CSS links.
(function () {
    try {
        var t = localStorage.getItem('cbm-theme');
        if (t === 'light' || t === 'dark') {
            document.documentElement.dataset.theme = t;
        }
    } catch (e) {}
})();
