/* Additional listing source pilot. Reads separate snapshots; no deal/cloud writes. */
(function (root) {
    'use strict';
    const SOURCE = 'Clear Choice / MLS';
    const ALLOWED = new Set(['roof_type','heating','cooling','parking','parking_spaces','construction','basement',
        'stories','water','sewer','beds','baths','sqft','year_built','total_rooms','style','lot_area_acres']);
    const ALIASES = {STREET:'ST',AVENUE:'AVE',ROAD:'RD',DRIVE:'DR',PLACE:'PL',BOULEVARD:'BLVD',LANE:'LN',COURT:'CT',TERRACE:'TER',
        NORTH:'N',SOUTH:'S',EAST:'E',WEST:'W',APARTMENT:'UNIT',APT:'UNIT',SUITE:'UNIT'};
    function norm(value) {
        return String(value || '').toUpperCase().replace(/#\s*/g,' UNIT ').replace(/\b([A-Z]+)\.(?=\s|$)/g,'$1')
            .replace(/,/g,' ').trim().split(/\s+/).map(w=>ALIASES[w] || w).join(' ');
    }
    function identity(p) { return [norm(p.address),norm(p.city),String(p.state || p.source_state || 'PA').toUpperCase(),String(p.zip || '').trim().slice(0,5)]; }
    function listing(p) { return String(p.listing_id || p.docket_id || p.id || '').match(/^(?:(?:PA-)?MLS-)?(\d+)$/)?.[1] || null; }
    function sourceUrl(value) {
        try { const u = new URL(value); return u.protocol === 'https:' && u.hostname === 'www.clearchoiceenterprises.com' &&
            !u.username && !u.password && (!u.port || u.port === '443') && !u.search && !u.hash &&
            /^\/idx\/[a-z0-9-]+\/\d+_spid\/$/.test(u.pathname) ? u.href : null; } catch (_) { return null; }
    }
    function inventoryUrl(value) {
        try { const u = new URL(value); return u.protocol === 'https:' && !u.username && !u.password && (!u.port || u.port === '443') ? u.href.replace(/#.*$/,'') : null; } catch (_) { return null; }
    }
    function matches(p, r) {
        const id = identity(p), county = String(p.county || '').toLowerCase().replace(/ county$/,'');
        return Boolean(!p._idAmbiguous && p.source_type === 'mls' && r?.provider === 'clearchoice' && r.status === 'published' &&
            r.property_id === String(p.id) && r.listing_id === listing(p) && sourceUrl(r.source_url) &&
            inventoryUrl(p.url) && r.inventory_source_url === inventoryUrl(p.url) &&
            JSON.stringify(r.identity) === JSON.stringify(id) && id[2] === 'PA' && /^\d{5}$/.test(id[3]) &&
            r.subject?.complete && ['allegheny','erie'].includes(county) && String(r.subject.county).toLowerCase() === county &&
            norm(r.subject.street + (r.subject.unit ? ' unit ' + r.subject.unit : '')) === id[0] &&
            norm(r.subject.city) === id[1] && r.subject.state === id[2] && r.subject.zip === id[3]);
    }
    function value(v) {
        if (typeof v === 'number') return Number.isFinite(v) ? String(v) : null;
        if (typeof v !== 'string') return null;
        const t = v.trim(); return t && t.length <= 1000 && !/^(unknown|n\/a|none|null|לא פורסם|לא ידוע|-+)$/i.test(t) ? t : null;
    }
    function fact(p, r, field) {
        if (!matches(p,r) || !ALLOWED.has(field)) return null;
        const e = r.facts?.[field];
        return e && value(e.value) !== null && e.source === SOURCE && e.source_url === r.source_url &&
            e.property_id === String(p.id) && e.listing_id === listing(p) && e.status === 'reported_by_source'
            ? {value:value(e.value),source:SOURCE,url:r.source_url,date:e.checked_at || r.retrieved_at,unit:e.unit || null} : null;
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
            if (e?.source_url !== r.source_url || e.listing_id !== listing(p) || !photoUrl(e.url,listing(p))) return false;
            const seq=e.url.match(/_(\d{2,3})\.jpg$/)[1]; if(seen.has(seq)) return false; seen.add(seq); return true;
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
    const api={identity,listing,sourceUrl,matches,fact,photos,photoUrl,key,comparable};
    if (typeof module !== 'undefined' && module.exports) { module.exports=api; return; }
    root.SecondarySourceEvidence=api;
    if (typeof document === 'undefined' || typeof openPropertyModal !== 'function') return;

    const records=new WeakMap(), baseFact=propertyTechnicalFact, baseOpen=openPropertyModal, baseTranslate=translateListingFact;
    translateListingFact=function(v) {
        const translations={'composition':'גג מחומר מרוכב','forced air, gas':'חימום אוויר מאולץ בגז','wall/window unit(s)':'יחידות קיר/חלון',
            'full, walk-out access':'מרתף מלא עם יציאה החוצה','public':'רשת ציבורית','colonial, ranch':'Colonial, Ranch'};
        return translations[String(v || '').toLowerCase()] || baseTranslate(v);
    };
    propertyTechnicalFact=function(p,field) {
        const original=baseFact(p,field);
        return original.source ? original : fact(p,records.get(p),field) || original;
    };
    const labels={roof_type:'סוג הגג',heating:'חימום',cooling:'קירור',parking:'חניה',parking_spaces:'מספר חניות',construction:'חומרי בנייה',
        basement:'מרתף',stories:'קומות',water:'מים',sewer:'ביוב',beds:'חדרי שינה',baths:'חדרי רחצה',sqft:'שטח מגורים (SqFt)',
        year_built:'שנת בנייה',total_rooms:'סך חדרים',style:'סגנון',lot_area_acres:'מגרש כפי שפורסם (Acres)'};
    function element(tag,text,className='') { const n=document.createElement(tag); n.textContent=text; n.className=className; return n; }
    function panel() {
        let n=document.getElementById('modal-additional-source');
        if (n) return n;
        const anchor=document.getElementById('modal-listing-source-controls'); if(!anchor) return null;
        n=element('section','','mt-3 p-3 rounded-xl border border-blue-500/30 bg-gray-950/40'); n.id='modal-additional-source'; n.dir='rtl';
        anchor.parentNode.appendChild(n); return n;
    }
    function notice(text) { const n=document.getElementById('modal-additional-source-notice'); if(n) n.textContent=text; }
    function render(p,message='') {
        const n=panel(); if(!n) return; n.replaceChildren();
        const supported=!p._idAmbiguous && p.source_type==='mls' && listing(p) && ['Allegheny','Erie'].includes(p.county);
        n.hidden=!supported; if(!supported) return;
        n.appendChild(element('h4','פרטים ממקור נוסף — ללא RentCast','text-blue-300 font-bold mb-2'));
        const button=element('button','טען ממקור נוסף','bg-blue-900/80 border border-blue-400/40 rounded-lg p-2 text-sm font-bold');
        button.type='button'; button.id='modal-additional-source-request'; button.addEventListener('click',request); n.appendChild(button);
        const status=element('p','','text-xs text-gray-400 my-2'); status.id='modal-additional-source-notice'; n.appendChild(status);
        const r=records.get(p);
        if(!matches(p,r)) { status.textContent=message || 'אין פרטים שמורים ממקור נוסף לנכס זה. הניסוי מחפש במקור Clear Choice בלבד.'; return; }
        const dated=parseDate(r.retrieved_at) ? displayDate(r.retrieved_at) : 'תאריך לא ידוע';
        status.textContent=message || 'פרטים שמורים ממודעת Clear Choice · נאספו: '+dated+
            (r.source_updated_at ? ' · עודכנו במקור: '+r.source_updated_at : '')+
            (parseDate(r.retrieved_at) && Date.now()-parseDate(r.retrieved_at)>7*86400000 ? ' · הנתונים בני יותר משבוע.' : '');
        const link=element('a','פתח את המודעה ואת התיאור המלא במקור','text-blue-300 underline text-xs');
        link.href=r.source_url; link.target='_blank'; link.rel='noopener noreferrer'; n.appendChild(link);
        const table=document.createElement('table'); table.className='w-full mt-3 text-xs';
        const head=document.createElement('tr'); ['נתון','במקור הקיים','Clear Choice'].forEach(t=>head.appendChild(element('th',t,'p-2 text-right text-gray-400'))); table.appendChild(head);
        for(const [field,label] of Object.entries(labels)) {
            const f=fact(p,r,field); if(!f) continue;
            const previous=baseFact(p,field), existing=previous.source ? previous.value : ['beds','baths','sqft','year_built','total_rooms'].includes(field) ? value(p[field]) : null;
            const conflict=existing !== null && existing !== undefined && comparable(existing)!==comparable(f.value);
            const tr=document.createElement('tr'); tr.className='border-t border-gray-800'+(conflict?' text-amber-300':'');
            tr.append(element('td',label,'p-2'),element('td',existing ? translateListingFact(existing) : 'לא פורסם','p-2'),
                element('td',translateListingFact(f.value)+(conflict?' · הבדל לבדיקה':''),'p-2')); table.appendChild(tr);
        }
        const comparison=document.createElement('details');
        comparison.appendChild(element('summary','פרטים מלאים והשוואה למקור הקיים','cursor-pointer text-blue-200 mt-3 text-xs'));
        comparison.appendChild(table); n.appendChild(comparison);
        n.appendChild(element('p','אכלוס ומצב הגג לא פורסמו במקור שנבדק. שטח מגרש ב־Acres נשמר ביחידה המקורית ללא המרה.','text-xs text-gray-400 mt-2'));
        const gallery=element('div','','grid grid-cols-1 md:grid-cols-3 gap-2 mt-3'); gallery.id='modal-additional-source-photos';
        for(const photo of photos(p,r)) {
            const a=document.createElement('a'); a.href=r.source_url; a.target='_blank'; a.rel='noopener noreferrer';
            const img=document.createElement('img'); img.src=photo.url; img.alt='תמונת המודעה של '+p.address;
            img.loading='lazy'; img.referrerPolicy='no-referrer'; img.className='w-full h-44 object-cover rounded-lg';
            img.addEventListener('error',()=>{img.hidden=true; a.appendChild(element('span','התמונה לא נטענה; פתח את המודעה במקור.','text-xs text-gray-400'));},{once:true});
            a.appendChild(img); gallery.appendChild(a);
        }
        n.appendChild(gallery);
        if(gallery.children.length) n.appendChild(element('p','התמונות מוצגות מהמודעה המקושרת. מועד הצילום לא פורסם.','text-xs text-gray-400 mt-1'));
    }
    async function load(p,generation) {
        try {
            const r=await readBasicSourceJson('COMPS_REPORTS/additional_sources/clearchoice/'+await key(p.id)+'.json');
            if(generation!==basicSourceGeneration || currentSelectedProperty!==p) return;
            if(matches(p,r)) records.set(p,r); else records.delete(p);
            renderPropertyTechnicalFacts(p); render(p);
        } catch(_) { if(generation===basicSourceGeneration && currentSelectedProperty===p) render(p,'הפרטים השמורים מהמקור הנוסף לא נטענו.'); }
    }
    openPropertyModal=function(id) {
        baseOpen(id); const p=currentSelectedProperty; if(!p) return;
        render(p); if(!p._idAmbiguous && p.source_type==='mls' && ['Allegheny','Erie'].includes(p.county)) load(p,basicSourceGeneration);
    };
    async function request() {
        const p=currentSelectedProperty,generation=basicSourceGeneration, token=localStorage.getItem('pa_github_token');
        if(!p || p._idAmbiguous) return;
        if(!token) { notice('להפעלת הבקשה יש לחבר את GitHub בהגדרות המערכת.'); return; }
        const button=document.getElementById('modal-additional-source-request'); if(button) button.disabled=true;
        const requestId=crypto.randomUUID();
        try {
            const response=await fetch('https://api.github.com/repos/elic555-ux/pa-intelligence-v2/actions/workflows/additional-property-source.yml/dispatches',{
                method:'POST',headers:{Authorization:'Bearer '+token,Accept:'application/vnd.github+json','Content-Type':'application/json'},
                body:JSON.stringify({ref:'main',inputs:{mode:'fetch',property_id:String(p.id),source_url:'',request_id:requestId}})});
            if(!response.ok) throw new Error('GitHub HTTP '+response.status);
            if(generation!==basicSourceGeneration || currentSelectedProperty!==p) return;
            notice('הבקשה נשלחה. המערכת בודקת מטמון לפני פנייה למקור הנוסף — ללא RentCast.');
            for(let i=0;i<48;i++) {
                await new Promise(resolve=>setTimeout(resolve,5000));
                if(generation!==basicSourceGeneration || currentSelectedProperty!==p) return;
                const status=await readBasicSourceJson('COMPS_REPORTS/additional_source_status.json');
                if(status?.request_id!==requestId || status.property_id!==String(p.id)) continue;
                await load(p,generation);
                if(generation!==basicSourceGeneration || currentSelectedProperty!==p) return;
                const messages={cache_used:'נטענו פרטים שמורים. לא נעשתה פנייה נוספת למקור.',published:'נשמרו פרטים חדשים מהמקור הנוסף.',
                    source_blocked:'המקור חסם את הבקשה. נשמרה הפסקה של יום לפחות.',source_rate_limited:'המקור ביקש להמתין. המערכת עצרה.',
                    source_cooldown:'המקור נמצא בהמתנה אחרי בקשה קודמת. הפרטים השמורים נשארים זמינים.',robots_disallowed:'המקור אינו מתיר גישה אוטומטית לעמוד.',
                    source_listing_not_found_in_pilot_pages:'הנכס לא נמצא בשלושת דפי הגילוי של הניסוי. אפשר להזין קישור Clear Choice ב־Actions.',
                    source_identity_mismatch:'המודעה הנוספת לא תאמה לזהות הנכס; הפרטים לא צורפו.',
                    ambiguous_property_identity:'זהות הנכס נמצאת בבדיקה; לא בוצעה פנייה למקור.',
                    ambiguous_or_missing_property_id:'מזהה הנכס חסר או שייך לכמה רשומות; לא בוצעה פנייה למקור.',
                    unsupported_or_incomplete_identity:'זהות הנכס אינה מלאה או שהמקור אינו נתמך בניסוי.'};
                notice(messages[status.status] || 'לא התקבל מפרט נוסף בבקשה זו. אפשר לפתוח את המודעה במקור ולבדוק את הריצה ב־Actions.'); return;
            }
            notice('הבקשה עדיין לא הסתיימה. בדוק ב־Actions את Additional Property Source ואז פתח שוב את הנכס.');
        } catch(error) { if(generation===basicSourceGeneration && currentSelectedProperty===p) notice('לא ניתן להפעיל את הבקשה: '+error.message+'. בדוק שהקובץ additional-property-source.yml הותקן.'); }
        finally { if(generation===basicSourceGeneration && currentSelectedProperty===p) {const n=document.getElementById('modal-additional-source-request'); if(n) n.disabled=false;} }
    }
    root.SecondarySourceEvidence.load=load;
})(typeof window !== 'undefined' ? window : globalThis);
