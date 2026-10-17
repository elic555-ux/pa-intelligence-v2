(function (root, factory) {
    'use strict';
    const api = factory();
    if (typeof module === 'object' && module.exports) module.exports = api;
    else {
        root.PAStreetView = api;
        const start = () => api.mount(root, root.document);
        if (root.document.readyState === 'loading') root.document.addEventListener('DOMContentLoaded', start, {once: true});
        else start();
    }
}(typeof window === 'undefined' ? globalThis : window, function () {
    'use strict';
    const ENDPOINT = 'https://gisdata.alleghenycounty.us/arcgis/rest/services/EGIS/AC_AddressPointsWithLabels/MapServer/0/query';
    const ERIE_ENDPOINT = 'https://gis.eriecountypa.gov/server/rest/services/Hosted/ErieCountyParcels_Dec2025/FeatureServer/16/query';
    const CALLBACK = 'paStreetView';
    const TIMEOUT_MS = 12000;
    const EMPTY = /^(?:unknown|n\/?a|none|not available|not provided|pennsylvania|erie county|allegheny county|כתובת לא ידועה|לא ידוע|[-—])$/i;
    const SUFFIX = Object.freeze({STREET: 'ST', ST: 'ST', ROAD: 'RD', RD: 'RD', DRIVE: 'DR', DR: 'DR',
        AVENUE: 'AVE', AV: 'AVE', AVE: 'AVE', BOULEVARD: 'BLVD', BLVD: 'BLVD', LANE: 'LN', LN: 'LN',
        COURT: 'CT', CT: 'CT', CIRCLE: 'CIR', CIR: 'CIR', TERRACE: 'TER', TER: 'TER', PLACE: 'PL', PL: 'PL',
        PARKWAY: 'PKWY', PKWY: 'PKWY', HIGHWAY: 'HWY', HWY: 'HWY', TRAIL: 'TRL', TRL: 'TRL'});

    function clean(value) {
        const text = typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : '';
        return !text || EMPTY.test(text.replace(/^[ .,;:-]+|[ .,;:-]+$/g, '')) ? '' : text;
    }

    function words(value) {
        return clean(value).toUpperCase().replace(/[.,'"’]/g, '').replace(/\s+/g, ' ').trim();
    }

    function streetIdentity(value) {
        const text = clean(value).toUpperCase();
        // Preserve house suffix, range and fraction. Unsupported number forms
        // never become a guessed integer query to the county service.
        const match = /^(\d+(?:[A-Z]|[¼½¾])?(?:-\d+[A-Z]?)?(?: \d+\/\d+)?) (.+)$/.exec(text);
        if (!match || !/\p{L}/u.test(match[2])) return null;
        const tokens = words(match[2]).split(' ').map(token => token.replace(/^(\d+)(?:ST|ND|RD|TH)$/, '$1'));
        const last = /^(?:N|S|E|W|NE|NW|SE|SW)$/.test(tokens[tokens.length - 1]) ? tokens.length - 2 : tokens.length - 1;
        if (last >= 0 && SUFFIX[tokens[last]]) tokens[last] = SUFFIX[tokens[last]];
        const house = match[1];
        const queryHouse = /^\d{1,8}$/.test(house) && Number(house) > 0 ? house : null;
        return {house, street: tokens.join(' '), key: house + ' ' + tokens.join(' '), queryHouse};
    }

    function localityKey(value) {
        return words(value).replace(/^CITY OF /, '').replace(/ (?:MUNICIPALITY|BOROUGH|BORO|TOWNSHIP|TWP|CITY)$/, '').trim();
    }

    function readRequest(search) {
        const params = new URLSearchParams(search);
        for (const key of ['address', 'city', 'zip', 'county', 'municipality']) if (params.getAll(key).length > 1) return null;
        const address = clean(params.get('address'));
        const city = clean(params.get('city'));
        const municipality = clean(params.get('municipality'));
        const zip = clean(params.get('zip'));
        const countyValue = (params.get('county') || '').replace(/\s+/g, ' ').trim();
        const county = /^(?:unknown|n\/?a|none|not available|not provided)$/i.test(countyValue) ? ''
            : countyValue.replace(/ County$/i, '').toLowerCase();
        const identity = streetIdentity(address);
        if (!identity || address.length > 250 || city.length > 100 || municipality.length > 100 || county.length > 100) return null;
        if (zip && !/^\d{5}(?:-\d{4})?$/.test(zip)) return null;
        if (!zip && !localityKey(municipality || city)) return null;
        const query = [address, city || municipality, ['PA', zip].filter(Boolean).join(' '), 'USA'].filter(Boolean).join(', ');
        const searchUrl = 'https://www.google.com/maps/search/?' + new URLSearchParams({api: '1', query});
        if (searchUrl.length > 2048) return null;
        return {address, city, municipality, zip, zip5: zip.slice(0, 5), county, identity, query, searchUrl};
    }

    function countyProvider(request) {
        if (!request) return null;
        if (request.county) return ['allegheny', 'erie'].includes(request.county) ? request.county : null;
        // Old links have no county parameter. These are selection hints only;
        // every returned record must still match the exact address and ZIP/city.
        return request.zip5.startsWith('15') || ['PITTSBURGH', 'PENN HILLS', 'VERONA'].includes(localityKey(request.city)) ? 'allegheny' : null;
    }

    function supportsCounty(request) {
        return countyProvider(request) !== null;
    }

    function countyRequestUrl(request) {
        if (!request || !request.identity.queryHouse || !supportsCounty(request)) return null;
        if (countyProvider(request) === 'erie') {
            if (!localityKey(request.municipality || request.city)) return null;
            // Public parcel fullstreet values contain variable internal spaces.
            // One bounded numeric-house prefix query avoids street-text SQL and
            // client validation still requires exact full street + municipality.
            const params = new URLSearchParams({where: "UPPER(fullstreet) LIKE '" + request.identity.queryHouse + "%'",
                outFields: 'fid,taxpin,municipali,fullstreet', outSR: '4326', returnCentroid: 'true',
                returnGeometry: 'false', resultRecordCount: '100', f: 'json'});
            return ERIE_ENDPOINT + '?' + params;
        }
        // Only validated decimal house numbers and ZIP digits reach SQL.
        const where = 'ADDR_NUM=' + request.identity.queryHouse + (request.zip5 ? " AND ZIP_CODE='" + request.zip5 + "'" : '');
        const params = new URLSearchParams({where,
            outFields: 'ADDR_NUM,FULL_ADDRESS,ZIP_CODE,STATE,MUNICIPALITY,STATUS',
            outSR: '4326', returnGeometry: 'true', resultRecordCount: '100',
            f: 'json', callback: CALLBACK});
        return ENDPOINT + '?' + params;
    }

    function validPoint(point) {
        return point && Number.isFinite(point.lat) && Number.isFinite(point.lng)
            && point.lat >= 39.70 && point.lat <= 42.30 && point.lng >= -80.53 && point.lng <= -74.68;
    }

    function panoramaUrl(point) {
        if (!validPoint(point)) return null;
        return 'https://www.google.com/maps/@?' + new URLSearchParams({api: '1', map_action: 'pano',
            viewpoint: point.lat.toFixed(7) + ',' + point.lng.toFixed(7)});
    }

    function wgs84Reference(reference) {
        if (!reference || typeof reference !== 'object') return false;
        const identifiers = [reference.wkid, reference.latestWkid].filter(value => value !== undefined);
        return identifiers.length > 0 && identifiers.every(value => value === 4326);
    }

    function validateCountyResponse(payload, request) {
        if (!request || !supportsCounty(request) || !payload || typeof payload !== 'object' || payload.error) return null;
        if (payload.exceededTransferLimit !== undefined && payload.exceededTransferLimit !== false) return null;
        if (!Array.isArray(payload.features) || payload.features.length > 100) return null;
        const sr = payload.spatialReference;
        if (!wgs84Reference(sr)) return null;
        if (countyProvider(request) === 'erie') return validateErieFeatures(payload, request);
        const matches = [];
        for (const feature of payload.features) {
            const a = feature && feature.attributes;
            const g = feature && feature.geometry;
            if (!a || !g || words(a.STATE) !== 'PA' || words(a.STATUS) !== 'ACTIVE') continue;
            if (g.spatialReference && !wgs84Reference(g.spatialReference)) continue;
            const full = streetIdentity(a.FULL_ADDRESS);
            if (!full || full.key !== request.identity.key || String(a.ADDR_NUM).trim() !== request.identity.house) continue;
            const zip = typeof a.ZIP_CODE === 'number' && Number.isInteger(a.ZIP_CODE) ? String(a.ZIP_CODE) : clean(a.ZIP_CODE);
            if (!/^\d{5}(?:-\d{4})?$/.test(zip)) continue;
            if (request.zip5 ? zip.slice(0, 5) !== request.zip5 : localityKey(a.MUNICIPALITY) !== localityKey(request.municipality || request.city)) continue;
            const point = {lat: g.y, lng: g.x};
            if (!validPoint(point)) continue;
            matches.push({...point, matchedAddress: clean(a.FULL_ADDRESS), quality: 'county_address_point'});
        }
        // Multiple matching address points (including unit/entrance records)
        // are ambiguous; never guess which one represents this property.
        return matches.length === 1 ? matches[0] : null;
    }

    function validateErieFeatures(payload, request) {
        const expectedMunicipality = localityKey(request.municipality || request.city);
        if (!expectedMunicipality) return null;
        const matches = [];
        for (const feature of payload.features) {
            const a = feature && feature.attributes;
            const centroid = feature && feature.centroid;
            if (!a || !centroid) continue;
            const full = streetIdentity(a.fullstreet);
            if (!full || full.key !== request.identity.key || localityKey(a.municipali) !== expectedMunicipality) continue;
            if (centroid.spatialReference && !wgs84Reference(centroid.spatialReference)) continue;
            const point = {lat: centroid.y, lng: centroid.x};
            if (!validPoint(point)) continue;
            matches.push({...point, matchedAddress: clean(a.fullstreet), quality: 'county_parcel_centroid'});
        }
        return matches.length === 1 ? matches[0] : null;
    }

    function resolveErieFetch(request, url, env) {
        if (typeof env.window.fetch !== 'function' || typeof env.window.AbortController !== 'function') {
            env.onResult({status: 'unavailable'});
            return {cancel() {}};
        }
        let active = true;
        const controller = new env.window.AbortController();
        const finish = (status, point) => {
            if (!active) return;
            active = false;
            env.clearTimeout(timer);
            if (status !== 'matched') controller.abort();
            env.onResult({status, point});
        };
        const timer = env.setTimeout(() => finish('timeout'), TIMEOUT_MS);
        let operation;
        try {
            operation = env.window.fetch(url, {mode: 'cors', credentials: 'omit', referrerPolicy: 'no-referrer', signal: controller.signal});
        } catch (_) {
            finish('unavailable');
            return {cancel() { finish('cancelled'); }};
        }
        Promise.resolve(operation).then(response => {
            if (!active) return null;
            if (!response || response.ok !== true) throw new Error('County response unavailable');
            return response.json();
        }).then(payload => {
            if (!active) return;
            const point = validateCountyResponse(payload, request);
            finish(point ? 'matched' : 'unavailable', point);
        }).catch(() => { if (active) finish('unavailable'); });
        return {cancel() { finish('cancelled'); }};
    }

    function resolveOnce(request, environment) {
        const env = environment;
        const url = countyRequestUrl(request);
        if (!url) {
            env.onResult({status: 'unavailable'});
            return {cancel() {}};
        }
        if (countyProvider(request) === 'erie') return resolveErieFetch(request, url, env);
        let active = true;
        let timer = null;
        const script = env.document.createElement('script');
        const cleanup = () => {
            if (timer !== null) env.clearTimeout(timer);
            script.onerror = null;
            script.onload = null;
            if (script.parentNode) script.parentNode.removeChild(script);
            // A removed JSONP script can still finish. Keep a harmless callback
            // so a late server response can neither navigate nor throw.
            env.window[CALLBACK] = function () {};
        };
        const finish = (status, point) => {
            if (!active) return;
            active = false;
            cleanup();
            env.onResult({status, point});
        };
        env.window[CALLBACK] = payload => {
            if (!active) return;
            let point = null;
            try { point = validateCountyResponse(payload, request); } catch (_) {}
            finish(point ? 'matched' : 'unavailable', point);
        };
        script.async = true;
        script.referrerPolicy = 'no-referrer';
        script.src = url;
        script.onerror = () => finish('unavailable');
        script.onload = () => { if (active) finish('unavailable'); };
        timer = env.setTimeout(() => finish('timeout'), TIMEOUT_MS);
        try { env.document.head.appendChild(script); } catch (_) { finish('unavailable'); }
        return {cancel() { finish('cancelled'); }};
    }

    function mount(win, doc) {
        if (doc.documentElement.dataset.streetViewStarted === 'true') return;
        doc.documentElement.dataset.streetViewStarted = 'true';
        const status = doc.getElementById('street-view-status');
        const address = doc.getElementById('street-view-address');
        const fallback = doc.getElementById('street-view-fallback');
        const cancel = doc.getElementById('street-view-cancel');
        const request = readRequest(win.location.search);
        if (!request) {
            status.textContent = 'חסרה כתובת מלאה שניתן לבדוק. אפשר לסגור את הלשונית ולבחור נכס אחר.';
            return;
        }
        address.textContent = request.query;
        fallback.href = request.searchUrl;
        cancel.hidden = false;
        let controller;
        controller = resolveOnce(request, {window: win, document: doc,
            setTimeout: win.setTimeout.bind(win), clearTimeout: win.clearTimeout.bind(win),
            onResult(result) {
                cancel.hidden = true;
                const url = result.status === 'matched' ? panoramaUrl(result.point) : null;
                if (url) {
                    status.textContent = 'נמצאה נקודת כתובת או חלקה תואמת. פותחים את צילום הרחוב הזמין בקרבתה…';
                    try { win.location.replace(url); return; } catch (_) {}
                }
                fallback.hidden = false;
                status.textContent = result.status === 'cancelled' ? 'הבדיקה בוטלה. לא תתבצע פתיחה אוטומטית.'
                    : result.status === 'timeout' ? 'בדיקת הכתובת לא הסתיימה בזמן. אפשר לפתוח מפה לפי הכתובת.'
                    : 'לא נמצאה נקודת כתובת יחידה שניתן לאמת. אפשר לפתוח מפה לפי הכתובת.';
            }});
        cancel.addEventListener('click', () => controller.cancel(), {once: true});
        fallback.addEventListener('click', () => controller.cancel(), {once: true});
        win.addEventListener('pagehide', () => controller.cancel(), {once: true});
    }

    return {readRequest, streetIdentity, localityKey, countyProvider, supportsCounty, countyRequestUrl,
        validPoint, panoramaUrl, validateCountyResponse, resolveOnce, mount, TIMEOUT_MS};
}));
