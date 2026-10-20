(function (root, factory) {
  'use strict';
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.PARehabReport = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const VERSION = 1;
  const MAX_COST = 100000000;
  const CONDITIONS = {
    unassessed: 'טרם נבדק', repair: 'נדרש טיפול', good: 'תקין לפי הזנה ידנית', not_applicable: 'לא רלוונטי'
  };
  const GROUPS = [
    { id: 'systems', name: 'מערכות מרכזיות', subtitle: 'תשתיות, חללים וציוד קבוע', number: '01', items: [
      ['roof', 'גג'], ['kitchen', 'מטבח'], ['flooring', 'ריצוף'], ['hvac', 'חימום ומיזוג'],
      ['electrical_panel', 'לוח חשמל'], ['wiring', 'חיווט חשמל'], ['plumbing', 'אינסטלציה'], ['bathrooms', 'חדרי רחצה']
    ] },
    { id: 'interior', name: 'גימורי פנים', subtitle: 'גמרים, אבזור והכנה לאכלוס', number: '02', items: [
      ['walls', 'קירות ותקרות'], ['interior_paint', 'צביעת פנים'], ['lights', 'גופי תאורה'], ['appliances', 'מכשירי חשמל'],
      ['interior_doors', 'דלתות פנים'], ['cleaning', 'ניקיון ופינוי'], ['interior_other', 'עבודות פנים נוספות']
    ] },
    { id: 'exterior', name: 'חוץ ומבנה', subtitle: 'מעטפת, פתחים ופיתוח סביבתי', number: '03', items: [
      ['windows', 'חלונות'], ['siding', 'חיפוי חוץ'], ['brick', 'לבנים וחזית'], ['gutters', 'מרזבים'],
      ['masonry', 'עבודות בנייה ובטון'], ['yard', 'חצר וגינון'], ['exterior_paint', 'צביעת חוץ'],
      ['exterior_doors', 'דלתות חוץ'], ['exterior_other', 'עבודות חוץ נוספות']
    ] }
  ];
  const ITEMS = GROUPS.flatMap(group => group.items.map(([id, name]) => ({ id, name, group: group.id })));
  const ITEM_IDS = new Set(ITEMS.map(item => item.id));
  const instances = typeof WeakMap === 'function' ? new WeakMap() : null;
  const own = (object, key) => Object.prototype.hasOwnProperty.call(object, key);
  const object = value => value && typeof value === 'object' && !Array.isArray(value) ? value : {};
  const clone = value => JSON.parse(JSON.stringify(value));
  const escapeHTML = value => String(value == null ? '' : value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[char]);
  const rounded = value => Math.round((value + Number.EPSILON) * 100) / 100;
  const money = value => '$' + Number(value).toLocaleString('en-US', { maximumFractionDigits: 2 });

  function parseAmount(value, max) {
    if (value == null || (typeof value === 'string' && value.trim() === '')) return { value: null, valid: true };
    if (typeof value !== 'string' && typeof value !== 'number') return { value: null, valid: false };
    if (typeof value === 'string' && !/^\d+(?:\.\d{1,2})?$/.test(value.trim())) return { value: null, valid: false };
    const amount = Number(value);
    if (!Number.isFinite(amount) || amount < 0 || amount > max || Math.abs(amount * 100 - Math.round(amount * 100)) > 0.000001) return { value: null, valid: false };
    return { value: rounded(amount), valid: true };
  }
  function normalizedDate(value) {
    if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T/.test(value) || !Number.isFinite(Date.parse(value))) return null;
    return new Date(value).toISOString();
  }
  function normalizePlan(plan, propertyId) {
    const subject = String(propertyId == null ? '' : propertyId);
    const supplied = object(plan);
    const matches = subject !== '' && String(supplied.property_id == null ? '' : supplied.property_id) === subject;
    const source = matches ? supplied : {};
    const sourceItems = object(source.items);
    const result = { version: VERSION, property_id: subject, items: {}, contingency_pct: 0, updated_at: normalizedDate(source.updated_at), validation_errors: {} };
    ITEMS.forEach(item => {
      const entry = object(own(sourceItems, item.id) ? sourceItems[item.id] : null);
      const amount = parseAmount(entry.cost, MAX_COST);
      const condition = own(CONDITIONS, entry.condition) ? entry.condition : 'unassessed';
      let scope = typeof entry.scope === 'string' ? entry.scope.trim() : '';
      if (!amount.valid) result.validation_errors[item.id] = 'יש להזין סכום חיובי או אפס, עד שתי ספרות אחרי הנקודה.';
      if (entry.condition != null && !own(CONDITIONS, entry.condition)) result.validation_errors[item.id] = 'מצב הסעיף אינו תקין.';
      if (scope.length > 1000) { result.validation_errors[item.id] = 'תיאור העבודה מוגבל ל־1,000 תווים.'; scope = scope.slice(0, 1000); }
      result.items[item.id] = { cost: amount.value, condition, scope };
    });
    const contingency = parseAmount(source.contingency_pct, 100);
    if (!contingency.valid) result.validation_errors.contingency_pct = 'יש להזין אחוז בין 0 ל־100, עד שתי ספרות אחרי הנקודה.';
    result.contingency_pct = contingency.value == null ? 0 : contingency.value;
    return result;
  }
  function summarize(plan) {
    const normalized = normalizePlan(plan, object(plan).property_id);
    const groups = GROUPS.map(group => {
      const entries = group.items.map(([id]) => normalized.items[id]);
      const active = entries.filter(entry => entry.condition !== 'not_applicable');
      const priced = active.filter(entry => entry.cost !== null);
      return { id: group.id, name: group.name, active_count: active.length, priced_count: priced.length,
        excluded_count: entries.length - active.length, subtotal: rounded(priced.reduce((total, entry) => total + entry.cost, 0)) };
    });
    const active_count = groups.reduce((total, group) => total + group.active_count, 0);
    const priced_count = groups.reduce((total, group) => total + group.priced_count, 0);
    const subtotal = rounded(groups.reduce((total, group) => total + group.subtotal, 0));
    const contingency_amount = rounded(subtotal * normalized.contingency_pct / 100);
    return { groups, active_count, priced_count, excluded_count: ITEMS.length - active_count, subtotal,
      contingency_pct: normalized.contingency_pct, contingency_amount, total: rounded(subtotal + contingency_amount),
      complete: active_count > 0 && priced_count === active_count && Object.keys(normalized.validation_errors).length === 0,
      validation_errors: normalized.validation_errors };
  }
  function coverageText(summary) {
    if (!summary.active_count) return 'כל הסעיפים סומנו כלא רלוונטיים';
    return summary.priced_count + ' מתוך ' + summary.active_count + ' סעיפים רלוונטיים תומחרו';
  }
  function overviewHTML(plan) {
    const sum = summarize(plan);
    const amount = sum.priced_count ? money(sum.subtotal) : 'טרם תומחר';
    const all = sum.priced_count ? money(sum.total) : 'טרם תומחר';
    const groups = GROUPS.map(group => {
      const info = sum.groups.find(row => row.id === group.id);
      const width = sum.subtotal > 0 ? Math.max(0, Math.min(100, info.subtotal / sum.subtotal * 100)) : 0;
      return '<article class="rh-group-card rh-group-' + group.id + '"><div class="rh-group-card-top"><span class="rh-group-number">' + group.number + '</span><h3>' + group.name + '</h3></div><strong class="rh-group-amount" dir="ltr">' + (info.priced_count ? money(info.subtotal) : 'טרם תומחר') + '</strong><p>' + info.priced_count + ' מתוך ' + info.active_count + ' סעיפים תומחרו</p><div class="rh-bar-track" aria-hidden="true"><span style="width:' + width.toFixed(2) + '%"></span></div></article>';
    }).join('');
    return '<div class="rh-budget-summary"><section class="rh-total-card"><span class="rh-eyebrow">תקציב עבודה ידני · USD</span><h3>סכום הסעיפים שתומחרו</h3><strong class="rh-grand-amount" dir="ltr">' + amount + '</strong><p>' + coverageText(sum) + '</p><span class="rh-completeness' + (sum.complete ? ' rh-complete' : '') + '">' + (sum.complete ? 'כל הסעיפים הרלוונטיים תומחרו' : 'התקציב עדיין חלקי') + '</span></section><section class="rh-budget-breakdown"><h3>הרכב התקציב</h3><div><span>סעיפים שתומחרו</span><strong dir="ltr">' + amount + '</strong></div><div><span>רזרבה לפי הזנה ידנית <bdi>(' + sum.contingency_pct + '%)</bdi></span><strong dir="ltr">' + (sum.priced_count ? money(sum.contingency_amount) : '—') + '</strong></div><div class="rh-final-row"><span>סעיפים שתומחרו + רזרבה</span><strong dir="ltr">' + all + '</strong></div><p>הרזרבה מחושבת רק על הסעיפים שתומחרו. עלויות שלא הוזנו אינן כלולות.</p></section></div><div class="rh-group-cards">' + groups + '</div>';
  }
  function dateText(value) {
    const text = String(value || '');
    return /^\d{4}-\d{2}-\d{2}/.test(text) ? text.slice(0, 10) : text || 'לא נשמר תאריך';
  }
  function documentedHTML(totals) {
    const source = object(totals);
    return '<section class="rh-documented"><div class="rh-section-intro"><span class="rh-section-kicker">מקורות מתועדים</span><h3>סכומי שיפוץ שנשמרו בנפרד</h3><p>הסכומים הבאים מוצגים לעיון. הם אינם מתווספים לתקציב הידני.</p></div><div class="rh-documented-grid">' + [['flip', 'שיפוץ לפליפ'], ['rental', 'שיפוץ להשכרה']].map(([key, title]) => {
      const entry = object(source[key]);
      const amount = parseAmount(entry.value, MAX_COST);
      const meta = [entry.source_name || entry.sourceName, entry.observed_at || entry.sourceDate ? dateText(entry.observed_at || entry.sourceDate) : null, entry.document_ref || entry.documentRef].filter(Boolean);
      return '<article><h4>' + title + '</h4><strong dir="ltr">' + (amount.valid && amount.value !== null ? money(amount.value) : 'אין סכום מתועד') + '</strong><p>' + escapeHTML(meta.length ? meta.join(' · ') : 'לא נשמר מקור מתאים ועדכני') + '</p></article>';
    }).join('') + '</div></section>';
  }
  function inspectionHTML(inspection) {
    const source = object(inspection);
    const findings = object(source.findings);
    const available = ['main', 'interior', 'exterior'].some(key => typeof findings[key] === 'string' && findings[key].trim());
    const metadata = [source.source_name, source.observed_at ? dateText(source.observed_at) : null, source.document_ref].filter(Boolean);
    return '<section class="rh-inspection"><div class="rh-section-intro"><span class="rh-section-kicker">ממצאי בדיקה</span><h3>מה תועד בנכס</h3><p>' + escapeHTML(available ? metadata.join(' · ') || 'פרטי המקור לא נמסרו לתצוגה' : 'לא נשמרו ממצאי בדיקה מתועדים. רשימת הסעיפים אינה קביעה על מצב הנכס.') + '</p></div><div class="rh-inspection-grid">' + [['main', 'מערכות מרכזיות'], ['interior', 'גימורי פנים'], ['exterior', 'חוץ ומבנה']].map(([key, name]) => '<article><h4>' + name + '</h4><p>' + escapeHTML(typeof findings[key] === 'string' && findings[key].trim() ? findings[key].trim() : 'לא נשמר ממצא מתועד.') + '</p></article>').join('') + '</div></section>';
  }
  function itemHTML(item, plan, editable) {
    const entry = plan.items[item.id];
    const error = plan.validation_errors[item.id] || '';
    const conditionOptions = Object.entries(CONDITIONS).map(([value, label]) => '<option value="' + value + '"' + (value === entry.condition ? ' selected' : '') + '>' + label + '</option>').join('');
    const condition = '<span class="rh-static rh-condition-value">' + CONDITIONS[entry.condition] + '</span>' + (editable ? '<select class="rh-editor rh-condition-input" data-field="condition" aria-label="מצב ' + item.name + '">' + conditionOptions + '</select>' : '');
    const costText = entry.condition === 'not_applicable' ? 'מחוץ לתקציב' : entry.cost === null ? 'לא תומחר' : money(entry.cost);
    return '<tr data-item="' + item.id + '" class="' + (entry.condition === 'not_applicable' ? 'rh-excluded' : '') + '"><th scope="row"><span class="rh-item-name">' + item.name + '</span>' + condition + '</th><td class="rh-scope-cell" data-label="תיאור העבודה"><span class="rh-static rh-scope-value">' + escapeHTML(entry.scope || 'לא הוזן תיאור עבודה') + '</span>' + (editable ? '<textarea class="rh-editor rh-scope-input" rows="2" maxlength="1000" data-field="scope" placeholder="תיאור העבודה לפי בדיקה או תכנון" aria-label="תיאור עבודה — ' + item.name + '">' + escapeHTML(entry.scope) + '</textarea>' : '') + '</td><td class="rh-cost-cell" data-label="תקציב שהוזן"><span class="rh-static rh-cost-value" dir="ltr">' + costText + '</span>' + (editable ? '<label class="rh-editor rh-cost-input-wrap"><span aria-hidden="true">$</span><input type="text" inputmode="decimal" autocomplete="off" data-field="cost" aria-label="תקציב ' + item.name + ' בדולרים" value="' + (entry.cost === null ? '' : entry.cost) + '" placeholder="טרם תומחר"' + (entry.condition === 'not_applicable' ? ' disabled' : '') + '></label>' : '') + '<span class="rh-field-error" role="status">' + escapeHTML(error) + '</span></td></tr>';
  }
  function renderHTML(options, draft) {
    const opts = options || {};
    const property = object(opts.property);
    const suppliedPlan = draft || opts.plan;
    const plan = normalizePlan(suppliedPlan, property.id);
    const editable = !opts.readOnly;
    const address = property.address || 'מפרט הנכס';
    const location = [property.city, property.state, property.zip].filter(Boolean).join(' · ');
    return '<div class="rh-report' + (editable ? ' rh-editable' : ' rh-readonly') + '" dir="rtl"><header class="rh-hero"><div><span class="rh-eyebrow">PROPERTY REHAB REVIEW</span><h2>מפרט שיפוץ ותכנון תקציב</h2><p class="rh-property-address">' + escapeHTML(address) + '</p><p class="rh-property-location">' + escapeHTML(location) + '</p></div><div class="rh-hero-mark" aria-hidden="true"><span>24</span><small>סעיפי בדיקה</small></div></header><p class="rh-draft-notice">טיוטה ידנית לתכנון השיפוץ. אינה הצעת קבלן ואינה מעדכנת תחשיבי השקעה. מצב הסעיפים נקבע רק לפי הזנה ידנית.</p><div class="rh-overview">' + overviewHTML(suppliedPlan && String(suppliedPlan.property_id) === String(property.id) ? suppliedPlan : plan) + '</div><section class="rh-reserve-settings"><label><span>רזרבה לתכנון (%)</span>' + (editable ? '<input class="rh-editor" type="text" inputmode="decimal" autocomplete="off" data-field="contingency_pct" value="' + plan.contingency_pct + '" aria-label="אחוז רזרבה לתכנון" placeholder="0">' : '') + '<strong class="rh-static rh-reserve-value">' + plan.contingency_pct + '%</strong></label><p>אחוז לבחירתך, ללא ברירת מחדל כספית.</p><span class="rh-reserve-error rh-field-error" role="status">' + escapeHTML(plan.validation_errors.contingency_pct || '') + '</span></section><div class="rh-detail-heading"><span class="rh-section-kicker">פירוט העבודה</span><h3>רשימת הסעיפים</h3><p>סכום ריק = לא תומחר. אפס הוא סכום שהוזן במפורש. סעיף לא רלוונטי אינו נכלל בתקציב.</p></div>' + GROUPS.map(group => '<section class="rh-detail-group rh-group-' + group.id + '"><div class="rh-table-heading"><span class="rh-group-number">' + group.number + '</span><div><h3>' + group.name + '</h3><p>' + group.subtitle + '</p></div></div><table class="rh-items-table"><thead><tr><th scope="col">סעיף ומצב</th><th scope="col">תיאור העבודה</th><th scope="col">תקציב שהוזן</th></tr></thead><tbody>' + ITEMS.filter(item => item.group === group.id).map(item => itemHTML(item, plan, editable)).join('') + '</tbody></table></section>').join('') + inspectionHTML(opts.inspection) + documentedHTML(opts.documentedTotals) + '<footer class="rh-save-panel"><div><strong>' + (editable ? 'שמירת טיוטת השיפוץ' : 'תצוגת מפרט שמור') + '</strong><p class="rh-save-status" role="status" aria-live="polite">' + (plan.updated_at ? 'עודכן: ' + escapeHTML(dateText(plan.updated_at)) : 'עדיין לא נשמרה טיוטה לנכס') + '</p></div>' + (editable ? '<button type="button" class="rh-save-button">שמור טיוטת שיפוץ</button>' : '<span class="rh-readonly-badge">לצפייה בלבד</span>') + '</footer></div>';
  }
  function mount(element, options) {
    if (!element || typeof element.addEventListener !== 'function') throw new TypeError('A DOM element is required');
    if (instances && instances.has(element)) return instances.get(element).update(options);
    let opts = options || {};
    let propertyId = String(object(opts.property).id == null ? '' : object(opts.property).id);
    let draft = normalizePlan(opts.plan, propertyId);
    let dirty = false;
    let saving = false;
    let destroyed = false;
    let generation = 0;
    const status = (message, error) => {
      const node = element.querySelector('.rh-save-status');
      if (node) { node.textContent = message; node.classList.toggle('rh-error', !!error); }
    };
    function paint() { element.innerHTML = renderHTML(opts, draft); }
    function refreshSummary() {
      const normalized = normalizePlan(draft, propertyId);
      const overview = element.querySelector('.rh-overview');
      if (overview) overview.innerHTML = overviewHTML(draft);
      const reserveValue = element.querySelector('.rh-reserve-value');
      if (reserveValue) reserveValue.textContent = normalized.contingency_pct + '%';
      const reserveError = element.querySelector('.rh-reserve-error');
      if (reserveError) reserveError.textContent = normalized.validation_errors.contingency_pct || '';
      const reserveInput = element.querySelector('[data-field="contingency_pct"]');
      if (reserveInput) reserveInput.setAttribute('aria-invalid', String(!!normalized.validation_errors.contingency_pct));
      ITEMS.forEach(item => {
        const row = element.querySelector('[data-item="' + item.id + '"]');
        if (!row) return;
        const entry = normalized.items[item.id];
        row.classList.toggle('rh-excluded', entry.condition === 'not_applicable');
        row.querySelector('.rh-condition-value').textContent = CONDITIONS[entry.condition];
        row.querySelector('.rh-scope-value').textContent = entry.scope || 'לא הוזן תיאור עבודה';
        row.querySelector('.rh-cost-value').textContent = entry.condition === 'not_applicable' ? 'מחוץ לתקציב' : entry.cost === null ? 'לא תומחר' : money(entry.cost);
        const input = row.querySelector('[data-field="cost"]');
        if (input) { input.disabled = entry.condition === 'not_applicable' || !!opts.readOnly || saving; input.setAttribute('aria-invalid', String(!!normalized.validation_errors[item.id])); }
        row.querySelector('.rh-field-error').textContent = normalized.validation_errors[item.id] || '';
      });
    }
    function onInput(event) {
      if (opts.readOnly || saving || destroyed) return;
      const target = event.target;
      const field = target && target.getAttribute && target.getAttribute('data-field');
      if (!field) return;
      if (field === 'contingency_pct') draft.contingency_pct = target.value;
      else {
        const row = target.closest('[data-item]');
        const id = row && row.getAttribute('data-item');
        if (!ITEM_IDS.has(id) || !['cost', 'condition', 'scope'].includes(field)) return;
        draft.items[id][field] = target.value;
      }
      dirty = true;
      refreshSummary();
      status('יש שינויים שטרם נשמרו.', false);
    }
    async function onClick(event) {
      const button = event.target && event.target.closest && event.target.closest('.rh-save-button');
      if (!button || opts.readOnly || saving || destroyed) return;
      const normalized = normalizePlan(draft, propertyId);
      if (Object.keys(normalized.validation_errors).length) { refreshSummary(); status('יש לתקן את הסעיפים המסומנים לפני השמירה.', true); return; }
      if (!propertyId || typeof opts.onSave !== 'function') { status('שמירה אינה זמינה כרגע.', true); return; }
      const plan = clone(normalized);
      delete plan.validation_errors;
      plan.updated_at = new Date().toISOString();
      const savingId = propertyId;
      const savingGeneration = generation;
      const callback = opts.onSave;
      saving = true;
      element.querySelectorAll('.rh-editor,.rh-editor input,.rh-editor textarea,.rh-editor select,.rh-save-button').forEach(node => { if ('disabled' in node) node.disabled = true; });
      button.textContent = 'שומר…';
      status('שומר את הטיוטה…', false);
      try {
        await callback(plan);
        if (!destroyed && propertyId === savingId && generation === savingGeneration) { draft = normalizePlan(plan, propertyId); dirty = false; saving = false; paint(); status('טיוטת השיפוץ נשמרה. תחשיבי ההשקעה לא השתנו.', false); }
      } catch (error) {
        if (!destroyed && propertyId === savingId && generation === savingGeneration) { saving = false; paint(); status('הטיוטה לא נשמרה. ' + (error && error.message ? error.message : 'נסה שוב.'), true); }
      } finally {
        if (propertyId === savingId && generation === savingGeneration) saving = false;
      }
    }
    const api = {
      getPlan: () => { const plan = normalizePlan(draft, propertyId); delete plan.validation_errors; return clone(plan); },
      isDirty: () => dirty,
      update(nextOptions) {
        const next = nextOptions || {};
        const nextId = String(object(next.property).id == null ? '' : object(next.property).id);
        const changed = nextId !== propertyId;
        const readonlyChanged = !!next.readOnly !== !!opts.readOnly;
        opts = next;
        if (changed) { generation++; propertyId = nextId; draft = normalizePlan(opts.plan, propertyId); dirty = false; saving = false; paint(); }
        else if (!dirty && !saving) { draft = normalizePlan(opts.plan, propertyId); paint(); }
        else if (readonlyChanged && !saving) paint();
        return api;
      },
      destroy() { destroyed = true; element.removeEventListener('input', onInput); element.removeEventListener('change', onInput); element.removeEventListener('click', onClick); if (instances) instances.delete(element); }
    };
    element.addEventListener('input', onInput);
    element.addEventListener('change', onInput);
    element.addEventListener('click', onClick);
    if (instances) instances.set(element, api);
    paint();
    return api;
  }
  return { VERSION, GROUPS, ITEMS, CONDITIONS, normalizePlan, summarize, renderHTML, mount };
});
