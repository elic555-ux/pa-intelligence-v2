(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else root.PAStreetPreview = api.createPreview({window: root, document: root.document});
}(typeof window === 'undefined' ? globalThis : window, function () {
    'use strict';
    const TIMEOUT_MS = 12000;
    const REFERRER_POLICY = 'strict-origin-when-cross-origin';
    const VIEW = Object.freeze({heading: '0', pitch: '0', fov: '90'});

    function parseConfig(value) {
        if (!value || typeof value !== 'object' || value.enabled !== true || typeof value.apiKey !== 'string') return null;
        const apiKey = value.apiKey.trim();
        return /^AIza[A-Za-z0-9_-]{30,60}$/.test(apiKey) ? {apiKey} : null;
    }

    function addressQuery(destination) {
        if (!destination || typeof destination.query !== 'string') return null;
        const query = destination.query.replace(/\s+/g, ' ').trim();
        const first = query.split(',')[0];
        if (query.length > 1000 || !/\p{L}/u.test(first) || !/, PA(?: \d{5}(?:-\d{4})?)?, USA$/i.test(query)) return null;
        if (/^(?:unknown|n\/?a|none|not available|not provided)$/i.test(first)) return null;
        return query;
    }

    function panoId(value) {
        return typeof value === 'string' && /^[A-Za-z0-9_:/.-]{8,512}$/.test(value) ? value : null;
    }

    function validLocation(location) {
        return location && Number.isFinite(location.lat) && Number.isFinite(location.lng)
            && location.lat >= 39.70 && location.lat <= 42.30 && location.lng >= -80.53 && location.lng <= -74.68;
    }

    function metadataUrl(query, apiKey) {
        return 'https://maps.googleapis.com/maps/api/streetview/metadata?' + new URLSearchParams({location: query, key: apiKey});
    }

    function imageUrl(pano, apiKey) {
        return 'https://maps.googleapis.com/maps/api/streetview?' + new URLSearchParams({pano, key: apiKey,
            size: '640x360', return_error_code: 'true', ...VIEW});
    }

    function panoramaUrl(pano) {
        return 'https://www.google.com/maps/@?' + new URLSearchParams({api: '1', map_action: 'pano', pano, ...VIEW});
    }

    function createPreview(environment) {
        const win = environment.window;
        const doc = environment.document;
        let configPromise = null;
        let generation = 0;
        let activeQuery = null;
        let pending = null;

        function elements() {
            const container = doc.getElementById('modal-street-view-preview');
            const link = doc.getElementById('modal-street-view-preview-link');
            const image = doc.getElementById('modal-street-view-preview-image');
            return container && link && image ? {container, link, image} : null;
        }

        function hide() {
            const nodes = elements();
            if (!nodes) return;
            nodes.container.hidden = true;
            nodes.link.removeAttribute('href');
            nodes.image.onload = null;
            nodes.image.onerror = null;
            nodes.image.removeAttribute('src');
        }

        function cancel() {
            generation += 1;
            activeQuery = null;
            if (pending) {
                win.clearTimeout(pending.timer);
                pending.controller.abort();
                pending = null;
            }
            hide();
        }

        function loadConfig() {
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

        function prepareImage(pano, apiKey, token) {
            const nodes = elements();
            if (!nodes || token !== generation) return;
            // A fresh native image node makes stale load/error events belong to
            // the previous property. No image is downloaded into app storage.
            const image = nodes.image.cloneNode(false);
            image.removeAttribute('src');
            image.referrerPolicy = REFERRER_POLICY;
            image.loading = 'eager';
            image.decoding = 'async';
            image.alt = 'צילום רחוב בקרבת כתובת הנכס';
            nodes.image.parentNode.replaceChild(image, nodes.image);
            image.onload = () => {
                if (token !== generation || elements()?.image !== image) return;
                nodes.link.href = panoramaUrl(pano);
                nodes.container.hidden = false;
            };
            image.onerror = () => {
                if (token === generation && elements()?.image === image) hide();
            };
            image.src = imageUrl(pano, apiKey);
        }

        async function render(destination) {
            const query = addressQuery(destination);
            if (query && query === activeQuery) return;
            cancel();
            if (!query || !elements()) return;
            activeQuery = query;
            const token = generation;
            try {
                const config = await loadConfig();
                if (!config || token !== generation) return;
                const controller = new win.AbortController();
                const timer = win.setTimeout(() => { controller.abort(); if (token === generation) hide(); }, TIMEOUT_MS);
                pending = {controller, timer};
                const response = await win.fetch(metadataUrl(query, config.apiKey), {mode: 'cors', credentials: 'omit',
                    referrerPolicy: REFERRER_POLICY, signal: controller.signal});
                if (token !== generation || controller.signal.aborted || !response || response.ok !== true) return;
                const metadata = await response.json();
                if (token !== generation || controller.signal.aborted || !metadata || metadata.status !== 'OK' || !validLocation(metadata.location)) return;
                const pano = panoId(metadata.pano_id);
                if (pano) prepareImage(pano, config.apiKey, token);
            } catch (_) {
                if (token === generation) hide();
            } finally {
                if (pending && token === generation) {
                    win.clearTimeout(pending.timer);
                    pending = null;
                }
            }
        }

        return {render, cancel};
    }

    return {createPreview, parseConfig, addressQuery, panoId, validLocation, metadataUrl, imageUrl, panoramaUrl, TIMEOUT_MS};
}));
