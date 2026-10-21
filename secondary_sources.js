/* Howard Hanna, ErieMoves, Tarasa and Clear Choice evidence. Version 1.6.0, 2026-10-14. Reads separate snapshots; no deal/cloud writes. */
(function (root) {
    'use strict';
    const PROVIDERS = {
        howardhanna:{name:'Howard Hanna / Greater Erie MLS',host:'www.howardhanna.com',pattern:/^\/property\/[a-z0-9-]+-\d{9,15}$/},
        eriemoves:{name:'ErieMoves / Coldwell Banker Select / MLS',host:'eriemoves.com',pattern:/^\/listing\/PA\/[A-Za-z0-9-]+\/[A-Za-z0-9-]+\/\d+$/},
        tarasa:{name:'Tarasa / River Point Realty / MLS',host:'www.tarasa.com',pattern:/^\/property-search\/detail\/56\/\d+\/[a-z0-9-]+\/$/},
        clearchoice:{name:'Clear Choice / MLS',host:'www.clearchoiceenterprises.com',pattern:/^\/idx\/[a-z0-9-]+\/\d+_spid\/$/}
    };
    const ALLOWED = new Set(['roof_type','heating','cooling','parking','parking_spaces','construction','basement',
        'stories','water','sewer','beds','baths','sqft','year_built','total_rooms','style','lot_area_acres','hvac_type']);
    const ALIASES = {STREET:'ST',AVENUE:'AVE',ROAD:'RD',DRIVE:'DR',PLACE:'PL',BOULEVARD:'BLVD',LANE:'LN',COURT:'CT',TERRACE:'TER',
        NORTH:'N',SOUTH:'S',EAST:'E',WEST:'W',APARTMENT:'UNIT',APT:'UNIT',SUITE:'UNIT'};
    function norm(value) {
        return String(value || '').toUpperCase().replace(/#\s*/g,' UNIT ').replace(/\b([A-Z]+)\.(?=\s|$)/g,'$1')
            .replace(/,/g,' ').trim().split(/\s+/).map(w=>ALIASES[w] || w).join(' ');
    }
    function cityNorm(value) {
        return String(value || '').toUpperCase().replace(/([A-Z])\.(?=\s|$)/g,'$1').trim().replace(/\s+/g,' ');
    }
    function identity(p) { return [norm(p.address),cityNorm(p.city),String(p.state || p.source_state || 'PA').toUpperCase(),String(p.zip || '').trim().slice(0,5)]; }
    function listing(p) { return String(p.listing_id || p.docket_id || p.id || '').match(/^(?:(?:PA-)?MLS-)?(\d+)$/)?.[1] || null; }
    function sourceUrl(value,provider) {
        try { const u = new URL(value); return u.protocol === 'https:' &&
            Object.entries(PROVIDERS).some(([name,s])=>(!provider || provider===name) && u.hostname===s.host && s.pattern.test(u.pathname)) &&
            !u.username && !u.password && (!u.port || u.port === '443') && !u.search && !u.hash ? u.href : null; } catch (_) { return null; }
    }
    function inventoryUrl(value) {
        try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password && (!u.port || u.port === '443') ? u.href.replace(/#.*$/,'') : null; } catch (_) { return null; }
    }
    function erieUrlMatches(p,url) {
        const parts=new URL(url).pathname.split('/'), id=identity(p), tail=' '+id[3];
        const street=parts[4].replace(/-/g,' ');
        return cityNorm(parts[3].replace(/-/g,' '))===id[1] && street.endsWith(tail) && norm(street.slice(0,-tail.length))===id[0];
    }
    function hannaUrlMatches(p,url) {
        const id=identity(p), suffix='-'+id[1].toLowerCase().replace(/ /g,'-')+'-pa-'+id[3];
        const slug=new URL(url).pathname.slice('/property/'.length).replace(/-\d{9,15}$/,'');
        return slug.endsWith(suffix) && norm(slug.slice(0,-suffix.length).replace(/-/g,' '))===id[0];
    }
    function hannaPhoto(url) {
        try { const u=new URL(url); return u.protocol==='https:' && u.hostname==='photos.prod.cirrussystem.net' &&
            !u.username && !u.password && (!u.port || u.port==='443') && u.search==='?d=l' && !u.hash &&
            /^\/\d+\/[a-f0-9]{32}\/\d+\.jpeg$/.test(u.pathname); } catch (_) { return false; }
    }
    function bindingMatches(p, r) {
        const id = identity(p), county = String(p.county || '').toLowerCase().replace(/ county$/,'');
        return Boolean(!p._idAmbiguous && p.source_type === 'mls' && Object.hasOwn(PROVIDERS,r?.provider) &&
            r.property_id === String(p.id) && r.listing_id === listing(p) && sourceUrl(r.source_url,r.provider) &&
            (r.provider!=='eriemoves' || (county==='erie' && erieUrlMatches(p,r.source_url))) &&
            (r.provider!=='howardhanna' || (county==='erie' && hannaUrlMatches(p,r.source_url))) &&
            (r.provider!=='tarasa' || new URL(r.source_url).pathname.split('/')[4]===listing(p)) &&
            inventoryUrl(p.url) && r.inventory_source_url === inventoryUrl(p.url) &&
            JSON.stringify(r.identity) === JSON.stringify(id) && id[2] === 'PA' && /^\d{5}$/.test(id[3]) &&
            r.subject?.complete && ['allegheny','erie'].includes(county) && String(r.subject.county).toLowerCase() === county &&
            norm(r.subject.street + (r.subject.unit ? ' unit ' + r.subject.unit : '')) === id[0] &&
            cityNorm(r.subject.city) === id[1] && r.subject.state === id[2] && r.subject.zip === id[3]);
    }
    function matches(p,r) { return r?.status === 'published' && bindingMatches(p,r); }
    function value(v) {
        if (typeof v === 'number') return Number.isFinite(v) ? String(v) : null;
        if (typeof v !== 'string') return null;
        const t = v.trim(); return t && t.length <= 1000 && !/^(unknown|n\/a|none|null|לא פורסם|לא ידוע|-+)$/i.test(t) ? t : null;
    }
    function fact(p, r, field) {
        if (!matches(p,r) || !ALLOWED.has(field)) return null;
        const e = r.facts?.[field];
        return e && value(e.value) !== null && e.source === PROVIDERS[r.provider].name && e.source_url === r.source_url &&
            e.property_id === String(p.id) && e.listing_id === listing(p) && e.status === 'reported_by_source'
            ? {value:value(e.value),source:PROVIDERS[r.provider].name,url:r.source_url,date:e.checked_at || r.retrieved_at,unit:e.unit || null} : null;
    }
    function moxiPhoto(url) {
        try { const u=new URL(url); return u.protocol==='https:' && /^i\d+\.moxi\.onl$/.test(u.hostname) &&
            !u.username && !u.password && (!u.port || u.port==='443') && !u.search && !u.hash &&
            /^\/img-pr-\d+\/eri\/[a-f0-9]+\/\d+_\d+_full\.jpg$/.test(u.pathname); } catch (_) { return false; }
    }
    function photoUrl(url, mls) {
        try { const u = new URL(url), m = u.pathname.match(/^\/(?:pics[123]x|large)\/v\d+\/\d+\/\d+_(\d+)_(\d{2,3})\.jpg$/);
            return Boolean(u.protocol === 'https:' && u.hostname === 'cdn.listingphotos.sierrastatic.com' && !u.username && !u.password &&
                (!u.port || u.port === '443') && !u.search && !u.hash && m && m[1] === String(mls)); } catch (_) { return false; }
    }
    function photos(p,r) {
        if (!matches(p,r)) return [];
        const seen = new Set();
        return (Array.isArray(r.photos) ? r.photos : []).filter(e=>{
            if (e?.source !== PROVIDERS[r.provider].name || e.source_url !== r.source_url || e.listing_id !== listing(p) || !(r.provider==='eriemoves'?moxiPhoto(e.url):r.provider==='howardhanna'?hannaPhoto(e.url):photoUrl(e.url,listing(p)))) return false;
            const seq=['eriemoves','howardhanna'].includes(r.provider)?e.url:e.url.match(/_(\d{2,3})\.jpg$/)[1]; if(seen.has(seq)) return false; seen.add(seq); return true;
        }).slice(0,3);
    }
    async function key(propertyId, cryptoApi=root.crypto) {
        const bytes=new Uint8Array(await cryptoApi.subtle.digest('SHA-256',new TextEncoder().encode(String(propertyId))));
        return [...bytes].map(b=>b.toString(16).padStart(2,'0')).join('').slice(0,32);
    }
    function comparable(v) {
        const t=String(v).trim(), n=Number(t.replace(/,/g,''));
        return t && Number.isFinite(n) ? String(n) : t.toLowerCase().replace(/\s+/g,' ');
    }
    function ordered(p,items) {
        return (Array.isArray(items)?items:[]).filter(r=>matches(p,r)).sort((a,b)=>
            (Date.parse(b.retrieved_at)||0)-(Date.parse(a.retrieved_at)||0));
    }
    function bestFact(p,items,field) {
        for(const r of ordered(p,items)) { const f=fact(p,r,field); if(f) return f; }
        return null;
    }
    function combinedPhotos(p,items) {
        const seen=new Set();
        return ordered(p,items).flatMap(r=>photos(p,r)).filter(photo=>{
            const sequence=photo.url.match(/_(\d{2,3})\.jpg$/)?.[1] || photo.url;
            if(seen.has(sequence)) return false;
            seen.add(sequence); return true;
        }).slice(0,3);
    }
    function collectionNotice(p,items) {
        const saved=ordered(p,items);
        const fields=[...ALLOWED].filter(field=>saved.some(r=>fact(p,r,field))).length;
        const images=combinedPhotos(p,saved).length;
        const exhausted=(Array.isArray(items)?items:[]).some(r=>bindingMatches(p,r) &&
            r.last_attempt?.completion_status==='not_completed');
        if(fields || images) return 'שדות שנאספו: '+fields+' · קישורי תמונות: '+images+'. המידע יכול להיות חלקי; מוצגים רק פרטים עם מקור.'+
            (exhausted ? ' השלמת הפרטים לא הצליחה; הנתונים השמורים נשארים זמינים.' : '');
        const reasons={
            source_blocked:'ניסיון האיסוף האחרון מהמקור נחסם; לא התקבל מפרט נוסף לנכס זה.',
            source_rate_limited:'המקור ביקש להמתין בניסיון האיסוף האחרון.',
            robots_disallowed:'המקור אינו מתיר איסוף אוטומטי מהעמוד.',
            source_identity_mismatch:'המודעה שנבדקה לא התאימה לזהות הנכס; הפרטים לא צורפו.',
            listing_not_available:'המודעה לא הייתה זמינה בניסיון האיסוף האחרון.',
            source_unavailable:'המקור לא היה זמין בניסיון האיסוף האחרון.'
        };
        const attempts=(Array.isArray(items)?items:[]).filter(r=>bindingMatches(p,r))
            .map(r=>({status:r.last_attempt?.status || r.status,
                date:Date.parse(r.last_attempt?.checked_at || r.retrieved_at)||0}))
            .filter(r=>Object.hasOwn(reasons,r.status)).sort((a,b)=>b.date-a.date);
        if(exhausted) return 'השלמת הפרטים לא הצליחה. האיסוף לנכס זה הופסק; הסריקה ממשיכה לנכסים אחרים.';
        if(attempts.length) return reasons[attempts[0].status];
        if(saved.length) return 'מודעת המקור אומתה, אך לא נשמרו ממנה שדות מפרט או קישורי תמונות להצגה.';
        if(String(p.county || '').toLowerCase().replace(/ county$/,'')==='erie')
            return 'אין מפרט נוסף שמור לנכס זה. השלמה אוטומטית ממקור נוסף עדיין אינה זמינה למחוז Erie.';
        return 'טרם נאסף מפרט נוסף לנכס זה. ההשלמה תלויה בגישה למקור ובמידע שהוא מפרסם.';
    }
    function fallbackPhotos(p,record) {
        if (!record || record.schema!==1 || p._idAmbiguous || p.source_type!=='mls' ||
            record.property_id!==String(p.id) || record.listing_id!==listing(p) ||
            JSON.stringify(record.identity)!==JSON.stringify(identity(p)) ||
            record.county!==String(p.county || '').replace(/ County$/,'') ||
            record.inventory_source_url!==inventoryUrl(p.url)) return [];
        const result=[],seen=new Set();
        for (const photo of Array.isArray(record.photos)?record.photos:[]) {
            try {
                const image=new URL(photo.url),source=new URL(photo.source_url);
                const provider=photo.provider,check=photo.verification;
                const sourceAllowed=provider==='saved_source' ? Boolean(sourceUrl(source.href)) :
                      provider==='preferredrealty' ? source.hostname==='www.thepreferredrealty.com' &&
                          source.pathname==='/real-estate/'+listing(p)+'/' :
                    provider==='coldwellbanker' ? source.hostname==='www.coldwellbankerhomes.com' && /^\/pa\/[a-z0-9-]+\/[a-z0-9-]+\/pid_\d+\/$/.test(source.pathname) :
                    provider==='weichert' && source.hostname==='www.weichert.com' && /^\/\d+\/?$/.test(source.pathname);
                if(!sourceAllowed || source.protocol!=='https:' || source.username || source.password || source.search || source.hash ||
                    (source.port && source.port!=='443') || photo.listing_id!==listing(p) ||
                    image.protocol!=='https:' || image.username || image.password || image.search || image.hash ||
                    (image.port && image.port!=='443') ||
                      !(provider==='preferredrealty' ? image.hostname==='api.cotality.com' &&
                          /^\/trestle\/Media\/Property\/(?:PHOTO-Jpeg\/\d+\/\d+|\d+)\/(?:[A-Za-z0-9_-]+\/){2}[A-Za-z0-9_-]+$/.test(image.pathname) :
                          ['cdn.listingphotos.sierrastatic.com','m.cbhomes.com','m1.cbhomes.com','m2.cbhomes.com','m3.cbhomes.com','d36xftgacqn2p.cloudfront.net'].includes(image.hostname) &&
                          /^\/[A-Za-z0-9_./-]+\.(jpg|jpeg|png|webp)$/i.test(image.pathname)) ||
                    check?.status!=='image_verified' || !['image/jpeg','image/png','image/webp'].includes(check.content_type) ||
                    !/^[a-f0-9]{64}$/.test(check.sha256) || !(check.bytes>=100 && check.bytes<=3000000) ||
                    !Number.isFinite(Date.parse(photo.checked_at)) || seen.has(image.href)) continue;
                seen.add(image.href);result.push(photo);
            } catch(_) {}
        }
        return result.slice(0,3);
    }
    function fallbackNotice(p,record) {
        if(!record || record.property_id!==String(p.id) ||
            JSON.stringify(record.identity)!==JSON.stringify(identity(p)) ||
            record.inventory_source_url!==inventoryUrl(p.url)) return '';
        const count=fallbackPhotos(p,record).length;
        if(count) return 'בדיקת מקורות נוספים: '+count+' קישורי תמונות החזירו קובץ תמונה בעת הבדיקה; הטעינה בדפדפן נבדקת בנפרד.';
        const attempts=Array.isArray(record.attempts)?record.attempts:[];
          return attempts.length ? 'מקורות תמונות שנבדקו: '+attempts.map(a=>({preferredrealty:'The Preferred Realty',saved_source:'המקור השמור',coldwellbanker:'Coldwell Banker',weichert:'Weichert'}[a.provider] || '')).filter(Boolean).join(', ')+
            '. '+(record.sources_exhausted?'לא נמצאו תמונות זמינות במקורות שנבדקו; הסריקה ממשיכה.':'טרם הושלמו תמונות זמינות; הסריקה ממשיכה.') : '';
    }
    function originalPhotos(p) {
        const record=p.listing_source_details, source=inventoryUrl(p.url);
        if(p._idAmbiguous || p.source_type!=='mls' || record?.status!=='published' ||
            record.source_url!==source || record.listing_id!==listing(p) ||
            JSON.stringify(record.identity)!==JSON.stringify(identity(p))) return [];
        try {
            const url=new URL(source);
            if(!['redfin.com','www.redfin.com'].includes(url.hostname) || url.search ||
                !/^\/PA\/[^/]+\/[^/]+\/home\/\d+$/.test(url.pathname)) return [];
        } catch(_) { return []; }
        const seen=new Set();
        return (Array.isArray(p.listing_photos)?p.listing_photos:[]).filter(photo=>{
            try {
                const url=new URL(photo.url);
                if(photo.source_url!==source || photo.source!=='Redfin / MLS' ||
                    url.protocol!=='https:' || url.hostname!=='ssl.cdn-redfin.com' ||
                    url.username || url.password || (url.port && url.port!=='443') || url.search || url.hash ||
                    !url.pathname.startsWith('/photo/') || seen.has(url.href)) return false;
                seen.add(url.href); return true;
            } catch(_) { return false; }
        }).slice(0,3);
    }
    const api={originalPhotos,fallbackPhotos,fallbackNotice,identity,listing,sourceUrl,matches,fact,photos,photoUrl,key,comparable,ordered,bestFact,combinedPhotos,collectionNotice};
    if (typeof module !== 'undefined' && module.exports) { module.exports=api; return; }
    root.SecondarySourceEvidence=api;
    if (typeof document === 'undefined' || typeof openPropertyModal !== 'function') return;

    const technicalPanel=document.getElementById('modal-listing-source-controls')?.parentNode;
    if(technicalPanel) {
        technicalPanel.className+=' pa-technical-readable';
        const style=document.createElement('style');
        style.textContent=`
            .pa-technical-readable { font-size:14px; line-height:1.6; }
            .pa-technical-readable .text-xs { font-size:14px; line-height:1.6; }
            .pa-technical-readable .text-sm,
            .pa-technical-readable strong { font-size:16px; line-height:1.6; }
            .pa-technical-readable [class*="text-[10px]"],
            .pa-technical-readable [class*="text-[11px]"] {
                font-size:13px; line-height:1.6; overflow-wrap:anywhere;
            }
            .pa-technical-readable button { white-space:normal; }
            .pa-technical-readable td { overflow-wrap:anywhere; }
        `;
        technicalPanel.appendChild(style);
    }

    const photoReceipts=new WeakMap(), records=new WeakMap(), attempts=new WeakMap(), baseFact=propertyTechnicalFact, baseOpen=openPropertyModal, baseTranslate=translateListingFact;
    translateListingFact=function(v) {
        const translations={'composition':'גג מחומר מרוכב','forced air, gas':'חימום אוויר מאולץ בגז','wall/window unit(s)':'יחידות קיר/חלון',
            'full, walk-out access':'מרתף מלא עם יציאה החוצה','public':'רשת ציבורית','colonial, ranch':'Colonial, Ranch'};
        return translations[String(v || '').toLowerCase()] || baseTranslate(v);
    };
    propertyTechnicalFact=function(p,field) {
        const original=baseFact(p,field);
        return original.source ? original : bestFact(p,records.get(p),field) || original;
    };
    const labels={roof_type:'סוג הגג',heating:'חימום',cooling:'קירור',parking:'חניה',parking_spaces:'מספר חניות',construction:'חומרי בנייה',
        basement:'מרתף',stories:'קומות',water:'מים',sewer:'ביוב',beds:'חדרי שינה',baths:'חדרי רחצה',sqft:'שטח מגורים (SqFt)',
        year_built:'שנת בנייה',total_rooms:'סך חדרים',style:'סגנון',lot_area_acres:'מגרש כפי שפורסם (Acres)'};
    const baseSourceStatus=renderListingSourceStatus;
    renderListingSourceStatus=function(p,override='') {
        baseSourceStatus(p,override);
        if(override) return;
        const node=document.getElementById('modal-listing-source-status');
        if(!node) return;
        const items=ordered(p,records.get(p));
        const count=Object.keys(labels).filter(field=>items.some(r=>fact(p,r,field))).length;
        if(!count) return;
        const details=p.listing_source_details || {}, latest=details.last_attempt || {};
        const failures={source_unavailable:'עמוד המקור הראשי לא היה זמין.',
            source_blocked:'המקור הראשי חסם את הבקשה.',
            source_identity_mismatch:'המודעה במקור הראשי לא תאמה לזהות הנכס.'};
        const failure=failures[latest.status || details.status];
        node.textContent=`פרטים שמורים ממקור נוסף זמינים (${count} שדות).`+
            (failure ? ' ניסיון הרענון של המקור הראשי: '+failure+' הפרטים השמורים נשארים זמינים.' : '');
    };
    function element(tag,text,className='') { const n=document.createElement(tag); n.textContent=text; n.className=className; return n; }
    function panel() {
        let n=document.getElementById('modal-additional-source');
        if (n) return n;
        const anchor=document.getElementById('modal-listing-source-controls'); if(!anchor) return null;
        n=element('section','','mt-3 p-3 rounded-xl border border-blue-500/30 bg-gray-950/40'); n.id='modal-additional-source'; n.dir='rtl';
        anchor.parentNode.appendChild(n); return n;
    }
    function render(p,message='') {
        const n=panel(); if(!n) return; n.replaceChildren();
        const supported=!p._idAmbiguous && p.source_type==='mls' && listing(p) && ['Allegheny','Erie'].includes(p.county);
        n.hidden=!supported; if(!supported) return;
        n.appendChild(element('h4','מפרט ותמונות שנאספו','text-blue-300 font-bold mb-2'));
        const items=ordered(p,records.get(p)), r=items[0], receipt=photoReceipts.get(p), extras=fallbackPhotos(p,receipt), originals=originalPhotos(p);
        const origin=p.listing_identifier_origin;
        const originText=origin==='redfin_csv_mls_column' ? 'מספר המודעה התקבל מקובץ הסריקה של Redfin.' :
            origin==='address_fallback' ? 'מזהה הנכס נוצר מהכתובת כי מספר MLS לא הופיע בסריקה.' :
            'מקור מספר המודעה לא תועד בסריקה קודמת.';
        const verifiedNames=[...new Set([...items.map(item=>PROVIDERS[item.provider].name),...extras.map(photo=>photo.source_name)].filter(Boolean))];
        const verified=verifiedNames.length ? ' נמצאה מודעת מקור שנבדקה מול זהות הנכס: '+verifiedNames.join(' / ')+'.' :
            ' טרם נמצאה במערכת מודעת מקור נוספת שאומתה לזהות הנכס.';
        const identityStatus=element('p',originText+verified,'text-sm text-gray-300 my-2');
        identityStatus.id='modal-listing-identity-status'; n.appendChild(identityStatus);
        const status=element('p','','text-xs text-gray-400 my-2'); status.id='modal-additional-source-notice'; n.appendChild(status);
        const coverage=element('p',collectionNotice(p,attempts.get(p) || items)+' '+fallbackNotice(p,receipt),'text-sm text-gray-300 my-2');
        coverage.id='modal-source-coverage'; n.appendChild(coverage);
        if(!r && !extras.length && !originals.length) { status.textContent=message; return; }
        if(originals.length) {
            const originalSection=document.getElementById('modal-listing-photos-section');
            if(originalSection) originalSection.hidden=true;
            const link=element('a','פתח את המודעה: Redfin / MLS','block text-blue-300 underline text-xs');
            link.href=originals[0].source_url;link.target='_blank';link.rel='noopener noreferrer';n.appendChild(link);
        }
        const dated=parseDate(r?.retrieved_at) ? displayDate(r.retrieved_at) : 'תאריך לא ידוע';
        status.textContent=message || (!r ? (originals.length?'תמונות שמורות ממודעת Redfin / MLS של הנכס.':'תמונות ממקורות נוספים שנבדקו.') : 'פרטים שמורים ממודעת '+PROVIDERS[r.provider].name+' · נאספו: '+dated+
            (r.source_updated_at ? ' · עודכנו במקור: '+r.source_updated_at : '')+
            (parseDate(r.retrieved_at) && Date.now()-parseDate(r.retrieved_at)>7*86400000 ? ' · הנתונים בני יותר משבוע.' : ''));
        for(const item of items) {
            const link=element('a','פתח את המודעה: '+PROVIDERS[item.provider].name,'block text-blue-300 underline text-xs');
            link.href=item.source_url; link.target='_blank'; link.rel='noopener noreferrer'; n.appendChild(link);
        }
        for(const photo of extras.filter((photo,index,all)=>all.findIndex(other=>other.source_url===photo.source_url)===index &&
            !items.some(item=>item.source_url===photo.source_url))) {
            const link=element('a','פתח את מודעת התמונות: '+photo.source_name,'block text-blue-300 underline text-xs');
            link.href=photo.source_url;link.target='_blank';link.rel='noopener noreferrer';n.appendChild(link);
        }
        const table=document.createElement('table'); table.className='w-full mt-3 text-xs';
        const head=document.createElement('tr'); ['נתון','במקור הקיים',...items.map(s=>PROVIDERS[s.provider].name)].forEach(t=>head.appendChild(element('th',t,'p-2 text-right text-gray-400'))); table.appendChild(head);
        for(const [field,label] of Object.entries(labels)) {
            const facts=items.map(s=>fact(p,s,field)); if(!facts.some(Boolean)) continue;
            const previous=baseFact(p,field), existing=previous.source ? previous.value : ['beds','baths','sqft','year_built','total_rooms'].includes(field) ? value(p[field]) : null;
            const compared=[existing,...facts.map(f=>f?.value)].filter(v=>v!==null && v!==undefined);
            const conflict=new Set(compared.map(comparable)).size>1;
            const tr=document.createElement('tr'); tr.className='border-t border-gray-800'+(conflict?' text-amber-300':'');
            tr.append(element('td',label,'p-2'),element('td',existing!==null && existing!==undefined ? translateListingFact(existing) : 'לא פורסם','p-2'));
            facts.forEach(f=>tr.appendChild(element('td',f ? translateListingFact(f.value)+(conflict?' · הבדל לבדיקה':'') : 'לא פורסם','p-2')));
            table.appendChild(tr);
        }
        const comparison=document.createElement('details');
        comparison.appendChild(element('summary','פרטים מלאים והשוואה למקור הקיים','cursor-pointer text-blue-200 mt-3 text-xs'));
        comparison.appendChild(table); n.appendChild(comparison);
        n.appendChild(element('p','סוג גג אינו בדיקה של מצב הגג; אין להסיק אכלוס מנתוני המפרט. שטח מגרש ב־Acres נשמר ביחידה המקורית ללא המרה.','text-xs text-gray-400 mt-2'));
        const gallery=element('div','','grid grid-cols-1 md:grid-cols-3 gap-2 mt-3'); gallery.id='modal-additional-source-photos';
        const seen=new Set();
        const images=[...originals,...extras,...combinedPhotos(p,items)].filter(photo=>!seen.has(photo.url) && seen.add(photo.url));
        let cursor=0, loaded=0, exhausted=0;
        const loading=element('p',images.length?'בודק טעינת תמונות בדפדפן…':'לא נשמרו קישורי תמונות זמינים.','text-xs text-gray-300 mt-2');
        loading.id='modal-photo-load-status';
        function update() {
            if(currentSelectedProperty!==p || document.getElementById('modal-additional-source-photos')!==gallery) return;
            loading.textContent=loaded ? 'תמונות שנטענו בדפדפן: '+loaded+'.' :
                exhausted>=Math.min(3,images.length) ? 'התמונות לא נטענו. אפשר לפתוח את המודעה במקור; אין שינוי במצב הסריקה.' : 'בודק טעינת תמונות בדפדפן…';
        }
        for(let slot=0;slot<Math.min(3,images.length);slot++) {
            const a=document.createElement('a'); a.target='_blank';a.rel='noopener noreferrer';
            const img=document.createElement('img');
            img.alt='תמונת המודעה של '+p.address;
            img.loading='lazy';img.referrerPolicy='no-referrer';img.className='w-full h-44 object-cover rounded-lg';
            function next() {
                if(cursor>=images.length) {
                    img.hidden=true;exhausted++;
                    a.appendChild(element('span','התמונה לא נטענה; פתח את המודעה במקור.','text-xs text-gray-400'));
                    update();return;
                }
                const photo=images[cursor++];
                a.href=photo.source_url;img.title=photo.source_name || photo.source || 'מודעת המקור';img.src=photo.url;
            }
            img.addEventListener('load',()=>{loaded++;update();},{once:true});
            img.addEventListener('error',()=>{
                if(currentSelectedProperty!==p || document.getElementById('modal-additional-source-photos')!==gallery) return;
                next();
            });
            a.appendChild(img);gallery.appendChild(a);next();
        }
        n.appendChild(gallery);n.appendChild(loading);
        if(gallery.children.length) n.appendChild(element('p','התמונות מוצגות מהמודעות המקושרות. מועד הצילום לא פורסם.','text-xs text-gray-400 mt-1'));

    }
    // The primary snapshot and secondary snapshots finish independently.
    // Rebuild the shared gallery when the original photos arrive later.
    const baseRenderListingPhotos=renderListingPhotos;
    renderListingPhotos=function(p) {
        baseRenderListingPhotos(p);
        if(currentSelectedProperty===p) render(p);
    };
    async function load(p,generation) {
        try {
            const cacheKey=await key(p.id);
            const responses=await Promise.allSettled(Object.keys(PROVIDERS).map(provider=>
                readBasicSourceJson('COMPS_REPORTS/additional_sources/'+provider+'/'+cacheKey+'.json')).concat([
                readBasicSourceJson('COMPS_REPORTS/listing_photos/'+cacheKey+'.json') ]));
            if(generation!==basicSourceGeneration || currentSelectedProperty!==p) return;
            const photoResponse=responses.pop();
            photoReceipts.set(p,photoResponse.status==='fulfilled'?photoResponse.value:null);
            const snapshots=responses.filter(r=>r.status==='fulfilled').map(r=>r.value);
            attempts.set(p,snapshots);
            records.set(p,ordered(p,snapshots));
            renderPropertyTechnicalFacts(p); renderListingSourceStatus(p); render(p);
        } catch(_) { if(generation===basicSourceGeneration && currentSelectedProperty===p) render(p,'הפרטים השמורים מהמקור הנוסף לא נטענו.'); }
    }
    openPropertyModal=function(id) {
        baseOpen(id); const p=currentSelectedProperty; if(!p) return;
        render(p); if(!p._idAmbiguous && p.source_type==='mls' && ['Allegheny','Erie'].includes(p.county)) load(p,basicSourceGeneration);
    };
    root.SecondarySourceEvidence.load=load;
})(typeof window !== 'undefined' ? window : globalThis);

