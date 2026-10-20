(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.PAStreetPreview = api.createPreview({window: root, document: root.document});
}(typeof window === 'undefined' ? globalThis : window, function () {
    'use strict';
    const TIMEOUT_MS = 12000;
    const REFERRER_POLICY = 'strict-origin-when-cross-origin';

    function parseConfig(value) {
        if (!value || typeof value !== 'object' || value.enabled !== true || value.provider !== 'maps-embed'
            || typeof value.apiKey !== 'string') return null;
        const apiKey = value.apiKey.trim();
        return /^AIza[A-Za-z0-9_-]{30,60}$/.test(apiKey) ? {apiKey} : null;
    }

    function addressQuery(destination) {
        if (!destination || typeof destination.query !== 'string') return null;
        const query = destination.query.replace(/\s+/g, ' ').trim();
        const first = query.split(',')[0];
        if (query.length > 1000 || !/\p{L}/u.test(first) || !/, PA(?: \d{5}(?:-\d{4})?)?, USA$/i.test(query)) return null;
        return /^(?:unknown|n\/?a|none|not available|not provided)$/i.test(first) ? null : query;
    }

    function validLocation(point) {
        return point && Number.isFinite(point.lat) && Number.isFinite(point.lng)
            && point.lat >= 39.70 && point.lat <= 42.30 && point.lng >= -80.53 && point.lng <= -74.68;
    }

    function coordinates(point) {
        return validLocation(point) ? point.lat.toFixed(7) + ',' + point.lng.toFixed(7) : null;
    }

    function embedUrl(point, apiKey) {
        const location = coordinates(point);
        return location ? 'https://www.google.com/maps/embed/v1/streetview?' + new URLSearchParams({key: apiKey, location}) : null;
    }

    function panoramaUrl(point) {
        const viewpoint = coordinates(point);
        return viewpoint ? 'https://www.google.com/maps/@?' + new URLSearchParams({api: '1', map_action: 'pano', viewpoint}) : null;
    }

    function createPreview(environment) {
        const win = environment.window;
        const doc = environment.document;
        let configPromise = null;
        let generation = 0;
        let activeSelection = null;
        let pointPending = null;
        let frameTimer = null;
        let currentDestination = null;

        function refresh() {
            const destination = currentDestination;
            cancel();
            if (destination) return render(destination);
        }

        function elements() {
            const container = doc.getElementById('modal-street-view-preview');
            const link = doc.getElementById('modal-street-view-preview-link');
            const frame = doc.getElementById('modal-street-view-preview-frame');
            return container && link && frame ? {container, link, frame} : null;
        }

        function hide() {
            if (frameTimer !== null) { win.clearTimeout(frameTimer); frameTimer = null; }
            const nodes = elements();
            if (!nodes) return;
            nodes.container.hidden = true;
            nodes.link.removeAttribute('href');
            nodes.frame.onload = null;
            nodes.frame.onerror = null;
            nodes.frame.removeAttribute('src');
        }

        function cancel() {
            currentDestination = null;
            generation += 1;
            activeSelection = null;
            if (pointPending) {
                const lookup = pointPending;
                pointPending = null;
                lookup.controller?.cancel();
                lookup.finish(null);
            }
            hide();
        }

        function loadConfig() {
            if (win.PAStreetLocalSettings) {
                try { return Promise.resolve(parseConfig(win.PAStreetLocalSettings.read())); }
                catch (_) { return Promise.resolve(null); }
            }
            if (configPromise) return configPromise;
            configPromise = (async () => {
                let timer = null;
                try {
                    if (typeof win.fetch !== 'function' || typeof win.AbortController !== 'function') return null;
                    const controller = new win.AbortController();
                    timer = win.setTimeout(() => controller.abort(), TIMEOUT_MS);
                    const url = new URL('street-view-preview-config.json', win.location.href);
                    if (url.origin !== new URL(win.location.href).origin) return null;
                    const response = await win.fetch(url.href, {mode: 'same-origin', credentials: 'omit', cache: 'no-store',
                        referrerPolicy: REFERRER_POLICY, signal: controller.signal});
                    if (controller.signal.aborted || !response || response.ok !== true) return null;
                    const value = await response.json();
                    return controller.signal.aborted ? null : parseConfig(value);
                } catch (_) {
                    return null;
                } finally {
                    if (timer !== null) win.clearTimeout(timer);
                }
            })();
            return configPromise;
        }

        function lookupTarget(destination, query, token) {
            const locator = environment.locator || win.PAStreetView;
            if (!locator || typeof locator.readRequest !== 'function' || typeof locator.resolveOnce !== 'function'
                || typeof destination.streetViewUrl !== 'string') return Promise.resolve(null);
            let request;
            try {
                const url = new URL(destination.streetViewUrl, win.location.href);
                const expected = new URL('street-view.html', win.location.href);
                if (url.origin !== expected.origin || url.pathname !== expected.pathname) return Promise.resolve(null);
                request = locator.readRequest(url.search);
                if (!request || request.query !== query || !locator.supportsCounty(request)) return Promise.resolve(null);
            } catch (_) { return Promise.resolve(null); }
            return new Promise(resolve => {
                let done = false;
                const lookup = {controller: null, finish(point) {
                    if (done) return;
                    done = true;
                    if (pointPending === lookup) pointPending = null;
                    resolve(token === generation ? point : null);
                }};
                pointPending = lookup;
                try {
                    lookup.controller = locator.resolveOnce(request, {window: win, document: doc,
                        setTimeout: win.setTimeout.bind(win), clearTimeout: win.clearTimeout.bind(win),
                        onResult(result) {
                            const point = result?.point;
                            const quality = request.county === 'erie' ? 'county_parcel_centroid' : 'county_address_point';
                            lookup.finish(result?.status === 'matched' && validLocation(point) && point.quality === quality ? point : null);
                        }});
                } catch (_) { lookup.finish(null); }
            });
        }

        function prepareFrame(point, apiKey, token) {
            const nodes = elements();
            if (!nodes || token !== generation) return;
            const frame = nodes.frame.cloneNode(false);
            frame.removeAttribute('src');
            frame.referrerPolicy = REFERRER_POLICY;
            frame.loading = 'eager';
            frame.allowFullscreen = true;
            frame.title = 'תצוגת רחוב אינטראקטיבית בקרבת הנכס';
            let active = true;
            const finish = loaded => {
                if (!active || token !== generation || elements()?.frame !== frame) return;
                active = false;
                if (frameTimer !== null) { win.clearTimeout(frameTimer); frameTimer = null; }
                if (!loaded) { hide(); return; }
                // The parent cannot inspect cross-origin imagery or verify its
                // coverage. Keep Google's own controls and notices unobscured.
                nodes.link.href = panoramaUrl(point);
                nodes.container.hidden = false;
            };
            frame.onload = () => finish(true);
            frame.onerror = () => finish(false);
            frameTimer = win.setTimeout(() => finish(false), TIMEOUT_MS);
            frame.src = embedUrl(point, apiKey);
            nodes.frame.parentNode.replaceChild(frame, nodes.frame);
        }

        async function render(destination) {
            const query = addressQuery(destination);
            const selection = query && typeof destination.streetViewUrl === 'string' ? query + '\n' + destination.streetViewUrl : null;
            if (selection && selection === activeSelection) return;
            cancel();
            currentDestination = destination;
            if (!query || !elements()) return;
            activeSelection = selection;
            const token = generation;
            try {
                const config = await loadConfig();
                if (!config || token !== generation) return;
                const target = await lookupTarget(destination, query, token);
                if (!target || token !== generation) return;
                const usage = environment.usage || win.PAStreetUsage;
                if (!usage || typeof usage.reserve !== 'function') {
                    const status = doc.getElementById('street-view-usage-status');
                    if (status) status.textContent = 'המונה המרכזי אינו זמין. לא נשלחה בקשת תצוגת רחוב.';
                    return;
                }
                // Reserve centrally before assigning a provider URL. A failed,
                // timed-out or cancelled attempt is deliberately not refunded.
                const allowed = await usage.reserve();
                if (allowed === true && token === generation) prepareFrame(target, config.apiKey, token);
            } catch (_) {
                if (token === generation) hide();
            }
        }

        return {render, cancel, refresh};
    }

    return {createPreview, parseConfig, addressQuery, validLocation, embedUrl, panoramaUrl, TIMEOUT_MS};
}));


