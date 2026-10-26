(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.PAReportFinancials=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){'use strict';
const FINANCIAL_MODEL_VERSION = 'documented-financials-v2-20261007';
const FINANCIAL_EVIDENCE_RULES = {
    rent: {field:'verified_monthly_rent', prefix:'verified_rent', period:'monthly', days:90, methods:['lease','rent_roll','market_rent_estimate']},
    arv: {field:'verified_arv', prefix:'verified_arv', period:'total', days:180, methods:['appraisal_after_repair','comparable_sales_after_repair']},
    flipRehab: {field:'verified_flip_rehab', prefix:'verified_rehab', period:'total', days:90, methods:['contractor_quote','inspection_scope_estimate']},
    rentalRehab: {field:'verified_rental_rehab', prefix:'verified_rehab', period:'total', days:90, methods:['contractor_quote','inspection_scope_estimate']},
    operatingExpenses: {period:'annual', days:90, methods:['operating_budget','documented_expenses']},
    vacancyLoss: {period:'annual', days:90, methods:['operating_budget','documented_expenses']},
    acquisitionCosts: {period:'total', days:90, methods:['closing_estimate','settlement_statement']},
    sellingCosts: {period:'total', days:90, methods:['selling_quote','closing_estimate']},
    holdingCosts: {period:'total', days:90, methods:['holding_budget','documented_expenses']},
    financingCosts: {period:'total', days:90, methods:['lender_quote','documented_expenses']}
};
function financialNumber(value) {
    if (typeof value === 'boolean' || value === null || value === undefined) return null;
    if (typeof value !== 'string' && typeof value !== 'number') return null;
    if (typeof value === 'number') return Number.isFinite(value) && Math.abs(value) <= Number.MAX_SAFE_INTEGER ? value : null;
    const text = String(value).replace(/[$,]/g, '').trim();
    if (!/^-?\d+(?:\.\d+)?$/.test(text)) return null;
    const n = Number(text); return Number.isFinite(n) && Math.abs(n) <= Number.MAX_SAFE_INTEGER ? n : null;
}
function positiveFinancialNumber(value) { const n=financialNumber(value);return n!==null&&n>0?n:null; }
function financialUrl(value) {
    if (typeof value !== 'string') return null;
    const text=value.trim();
    if (!/^https?:\/\/[^/?#]+/i.test(text) || /[\s<>"'\\]/.test(text)) return null;
    try { const u = new URL(text); return ['https:','http:'].includes(u.protocol) && u.hostname && !u.username && !u.password ? text : null; }
    catch (_) { return null; }
}
function financialDate(value) {
    const text = String(value || '');
    if (!/^\d{4}-\d{2}-\d{2}(?:T(?:[01]\d|2[0-3]):[0-5]\d:[0-5]\d(?:\.\d{1,3})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d))?$/.test(text)) return null;
    const datePart = new Date(text.slice(0,10)+'T00:00:00Z');
    if (!Number.isFinite(datePart.getTime()) || datePart.toISOString().slice(0,10)!==text.slice(0,10)) return null;
    const stamp = new Date(text).getTime();
    return Number.isFinite(stamp) ? stamp : null;
}
function financialSourceOnly(p) {
    const s = String(p.source_type || '').toLowerCase(), d = String(p.deal_type || '').toLowerCase();
    return s.includes('sheriff') || s.includes('tax') || d.includes('sheriff') || d.includes('tax') || d.includes('פיגורי מס') || d.includes('חוב מס') ||
        ['repository_minimum_bid','court_amount','opening_bid'].includes(p.source_amount_type);
}
function financialEvidence(p, key, now = Date.now()) {
    const rule = FINANCIAL_EVIDENCE_RULES[key];
    let entry = p.financial_evidence?.[key];
    if (!entry && rule.field && p[rule.field] !== undefined) {
        const prefix = rule.prefix;
        entry = {value:p[rule.field], source_name:p[prefix+'_source'], source_url:p[prefix+'_source_url'],
            document_ref:p[prefix+'_document_ref'], observed_at:p[prefix+'_observed_at'], method:p[prefix+'_method'],
            period:p[prefix+'_period'], status:p[prefix+'_status'], property_id:p[prefix+'_property_id'],
            zero_cost_confirmed:p[prefix+'_zero_cost_confirmed'], property_match_confirmed:p[prefix+'_property_match_confirmed']};
    }
    if (!entry || typeof entry !== 'object' || Array.isArray(entry)) return null;
    const value = financialNumber(entry.value), stamp = financialDate(entry.observed_at);
    const url = financialUrl(entry.source_url), documentRef = String(entry.document_ref || '').trim();
    if (value === null || value < 0 || (['rent','arv'].includes(key) && value <= 0)) return null;
    if (value === 0 && entry.zero_cost_confirmed !== true) return null;
    if (typeof entry.source_name !== 'string' || !entry.source_name.trim() || (!url && (typeof entry.document_ref !== 'string' || documentRef.length < 5))) return null;
    if (!p.id || String(entry.property_id || '') !== String(p.id)) return null;
    if (!['documented','verified','provider_estimate'].includes(entry.status) || !rule.methods.includes(entry.method)) return null;
    if (entry.property_match_confirmed !== true) return null;
    if (entry.source_name === 'RentCast' && key === 'rent' && stamp !== null && now-stamp > 30*86400000) return null;
    if (entry.period !== rule.period || stamp === null || stamp > now || now - stamp > rule.days * 86400000) return null;
    return {value,property_id:String(p.id),source_name:entry.source_name.trim(),source_url:url,document_ref:typeof entry.document_ref==='string'?documentRef:'',
        observed_at:entry.observed_at,method:entry.method,period:entry.period,status:entry.status,
        property_match_confirmed:true,zero_cost_confirmed:entry.zero_cost_confirmed===true,notes:typeof entry.notes==='string'?entry.notes:''};
}
function financialTarget(p, name, percentage = false) {
    const t = p.financial_targets;
    if (!t || t.confirmed !== true) return null;
    const value = financialNumber(t[name]);
    if (value === null || value < 0 || (percentage && (value <= 0 || value > 100))) return null;
    return value;
}
function financialAskingPrice(p, now=Date.now()) {
    if(financialSourceOnly(p))return null;
    const price=positiveFinancialNumber(p.price),checked=financialDate(p.last_source_check);
    if(price===null||checked===null||checked>now||now-checked>30*86400000||!financialUrl(p.url||p.source_url))return null;
    return price;
}
function financialSnapshot(p, now = Date.now()) {
    const sourceOnly = financialSourceOnly(p), evidence = {};
    for (const key of Object.keys(FINANCIAL_EVIDENCE_RULES)) evidence[key] = sourceOnly ? null : financialEvidence(p,key,now);
    const askingPrice = financialAskingPrice(p,now);
    const val = key => evidence[key]?.value ?? null;
    const rent = val('rent'), arv = val('arv'), rentalRehab = val('rentalRehab'), flipRehab = val('flipRehab');
    const operatingExpenses = val('operatingExpenses'), vacancyLoss = val('vacancyLoss');
    const acquisitionCosts = val('acquisitionCosts'), sellingCosts = val('sellingCosts');
    const holdingCosts = val('holdingCosts'), financingCosts = val('financingCosts');
    const annualRent = rent !== null ? rent * 12 : null;
    const grossYield = rent !== null && askingPrice !== null ? Math.round(rent*1200/askingPrice*10)/10 : null;
    const noi = annualRent !== null && operatingExpenses !== null && vacancyLoss !== null ? annualRent-operatingExpenses-vacancyLoss : null;
    const targetCap = financialTarget(p,'cap_rate_pct',true), targetProfit = financialTarget(p,'flip_profit_amount');
    const rentalInputs = [noi,rentalRehab,acquisitionCosts,targetCap];
    const flipInputs = [arv,flipRehab,acquisitionCosts,sellingCosts,holdingCosts,financingCosts,targetProfit];
    const rentalCeiling = rentalInputs.every(x=>x!==null) ? Math.floor(noi/(targetCap/100)-rentalRehab-acquisitionCosts) : null;
    const flipCeiling = flipInputs.every(x=>x!==null) ? Math.floor(arv-flipRehab-acquisitionCosts-sellingCosts-holdingCosts-financingCosts-targetProfit) : null;
    const allIn = [askingPrice,flipRehab,acquisitionCosts,holdingCosts,financingCosts].every(x=>x!==null) ? askingPrice+flipRehab+acquisitionCosts+holdingCosts+financingCosts : null;
    const flipProfit = [arv,allIn,sellingCosts].every(x=>x!==null) ? arv-allIn-sellingCosts : null;
    const targets=p.financial_targets?.confirmed===true?{confirmed:true,cap_rate_pct:targetCap,flip_profit_amount:targetProfit}:null;
    const inputs = {model_version:FINANCIAL_MODEL_VERSION, price:askingPrice, evidence, targets};
    return {inputs, evidence, mode:sourceOnly?'source_record_only':'documented_inputs_only', price:askingPrice,
        rent, yield:grossYield, arv, rentalRehab, flipRehab, annualRent, operatingExpenses, vacancyLoss, acquisitionCosts,
        sellingCosts, holdingCosts, financingCosts, noi, allIn, flipProfit,
        flipRoi:flipProfit!==null && allIn>0 ? Math.round(flipProfit/allIn*1000)/10 : null,
        capRate:noi!==null && askingPrice!==null ? Math.round(noi/askingPrice*1000)/10 : null,
        maoRental:rentalCeiling>0?rentalCeiling:null, maoFlip:flipCeiling>0?flipCeiling:null,
        rentalFeasible:rentalCeiling===null?null:rentalCeiling>0,flipFeasible:flipCeiling===null?null:flipCeiling>0,
        rentMethod:rent!==null?'documented_source_input':null, arvMethod:arv!==null?'documented_source_input':null,
        rehabMethod:flipRehab!==null || rentalRehab!==null?'documented_source_input':null,
        rentReason:sourceOnly?'רשומת מכרז או חוב.':'חסר מקור עדכני לשכירות בנכס.',
        targetCap, targetProfit};
}
function financialMoney(value) {
    return value === null || value === undefined ? 'אין נתון מבוסס' : '$'+Number(value).toLocaleString('en-US',{maximumFractionDigits:2});
}

return {financialSnapshot,financialEvidence,financialAskingPrice,financialNumber,financialDate,financialMoney};});

