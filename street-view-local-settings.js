(function (root, factory) {
    'use strict';
    if (typeof module === 'object' && module.exports) module.exports = factory;
    else root.PAStreetLocalSettings = factory(root, root.document);
}(typeof window === 'undefined' ? globalThis : window, function (win, doc) {
    'use strict';
    const slot = 'pa.streetView.browserKey.v1';
    function read() {
        try {
            const apiKey = win.localStorage.getItem(slot) || '';
            return /^AIza[A-Za-z0-9_-]{30,60}$/.test(apiKey)
                ? {enabled: true, provider: 'maps-embed', apiKey} : null;
        } catch (_) { return null; }
    }
    function save(value) {
        const key = String(value || '').trim();
        if (!/^AIza[A-Za-z0-9_-]{30,60}$/.test(key)) return 'invalid';
        try { win.localStorage.setItem(slot, key); return 'saved'; }
        catch (_) { return 'unavailable'; }
    }
    function clear() {
        try { win.localStorage.removeItem(slot); return true; }
        catch (_) { return false; }
    }
    if (doc) {
        const form = doc.getElementById('street-view-local-settings');
        const input = doc.getElementById('street-view-local-key');
        const status = doc.getElementById('street-view-local-status');
        const remove = doc.getElementById('street-view-local-remove');
        if (form && input && status && remove) {
            status.textContent = read() ? 'מפתח שמור בדפדפן זה.' : 'התצוגה כבויה עד להזנת מפתח בדפדפן זה.';
            form.addEventListener('submit', event => {
                event.preventDefault();
                const result = save(input.value);
                input.value = '';
                status.textContent = result === 'saved' ? 'נשמר בדפדפן זה. טוען תצוגה, אם זמין צילום רחוב.'
                    : result === 'invalid' ? 'המפתח שהוזן אינו במבנה המתאים.' : 'השמירה בדפדפן חסומה. לא נשמר מפתח.';
                if (result === 'saved') win.PAStreetPreview?.refresh();
            });
            remove.addEventListener('click', () => {
                const removed = clear();
                input.value = '';
                status.textContent = removed ? 'המפתח נמחק מדפדפן זה והתצוגה כבויה.' : 'לא ניתן למחוק את המפתח. בדקו את הגדרות הדפדפן.';
                if (removed) win.PAStreetPreview?.refresh();
            });
        }
    }
    return {read, save, clear};
}));
