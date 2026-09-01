// Rehab Estimator & Contractor Bid Sanity Check Module for PA Real Estate

const PA_REHAB_BENCHMARKS = {
    tierRates: {
        light: 30,     // $30 / sqft (Cosmetic / Refresh)
        moderate: 55,  // $55 / sqft (Kitchen, Baths, Floors, Paint)
        heavy: 100     // $100 / sqft (Full Gut / Down to Studs)
    },
    lineItems: {
        kitchen: 15000,
        bath: 7500,
        roof: 9500,
        hvac: 9000,
        electric: 4500,
        plumbing: 6000,
        windows: 650, // Per window unit
        flooringPerSqft: 6.5,
        paintPerSqft: 3.0,
        contingencyPercent: 0.15 // 15% Buffer
    }
};

let currentRehabProperty = null;

function openRehabEstimator(propertyId) {
    const property = pipelineDeals.find(d => String(d.id) === String(propertyId));
    if (!property) return;
    currentRehabProperty = property;

    const sqft = Number(property.sqft) || 1200;
    const beds = Number(property.beds) || 3;
    const baths = Number(property.baths) || 1;
    const askPrice = Number(property.price) || 80000;
    const defaultArv = Math.round(askPrice * 1.8);

    const modalHTML = `
    <div id="rehab-modal" class="fixed inset-0 bg-black/90 backdrop-blur-md z-50 flex items-center justify-center p-3 sm:p-5">
        <div class="bg-gray-900 border border-amber-500/40 rounded-2xl max-w-4xl w-full p-6 space-y-5 shadow-2xl relative max-h-[92vh] overflow-y-auto custom-scrollbar text-right">
            
            <!-- Header -->
            <div class="flex justify-between items-start border-b border-gray-800 pb-3.5">
                <div>
                    <div class="flex items-center gap-2 mb-1.5">
                        <span class="px-2.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-xs font-black">
                            🔨 מחירון שיפוץ ובקרת הצעות קבלן (PA Rehab Engine)
                        </span>
                        <span class="px-2.5 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30 text-xs font-bold font-mono">
                            ${sqft.toLocaleString()} SqFt | ${beds} חדרים | ${baths} אמבטיות
                        </span>
                    </div>
                    <h2 class="text-2xl font-black text-white">${property.address}</h2>
                    <p class="text-xs text-gray-400 mt-1">${property.city}, ${property.county} County, PA</p>
                </div>
                <button onclick="closeRehabModal()" class="text-gray-400 hover:text-white p-2 rounded-xl bg-gray-800"><i data-lucide="x" class="w-5 h-5"></i></button>
            </div>

            <!-- Total Rehab & Financial Formula Ribbon -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 bg-gray-950 p-4 rounded-xl border border-gray-800 text-center shadow-inner">
                <div>
                    <span class="text-xs font-bold text-gray-400 block mb-0.5">עלות שיפוץ משוערת</span>
                    <strong class="text-2xl font-black text-amber-400 drop-shadow" id="rehab-calc-total">$0</strong>
                </div>
                <div>
                    <span class="text-xs font-bold text-gray-400 block mb-0.5">שווי לאחר שיפוץ (ARV)</span>
                    <input type="number" id="rehab-arv-input" value="${defaultArv}" oninput="recalculateRehab()" class="w-28 text-center bg-gray-900 border border-gray-700 rounded-lg px-2 py-0.5 text-base font-black text-emerald-400">
                </div>
                <div>
                    <span class="text-xs font-bold text-gray-400 block mb-0.5">הצעה מקסימלית (MAO 70%)</span>
                    <strong class="text-xl font-extrabold text-blue-400" id="rehab-calc-mao">$0</strong>
                </div>
                <div>
                    <span class="text-xs font-bold text-gray-400 block mb-0.5">פער ממחיר המבוקש</span>
                    <strong class="text-xl font-extrabold" id="rehab-calc-spread">$0</strong>
                </div>
            </div>

            <!-- 1. Quick Base Scope (Per SqFt) -->
            <div class="bg-gray-950/80 p-4 rounded-xl border border-gray-800 space-y-2.5">
                <h4 class="text-xs font-black text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                    <i data-lucide="layers" class="w-4 h-4"></i> 1. בחר רמת שיפוץ בסיסית (לפי שטח הנכס):
                </h4>
                <div class="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
                    <label class="flex items-start gap-2.5 p-3 rounded-xl bg-gray-900 border border-gray-800 cursor-pointer hover:border-amber-500/50">
                        <input type="radio" name="rehab-scope-tier" value="light" onchange="onTierChange('light')" class="accent-amber-500 mt-1">
                        <div>
                            <strong class="text-white block font-bold">שיפוץ קוסמטי (Light)</strong>
                            <span class="text-gray-400 text-[11px] block">צבע, ריצוף LVP, רענון קל ($30/sqft)</span>
                            <span class="text-emerald-400 font-mono font-bold mt-1 block">~$${(sqft * 30).toLocaleString()}</span>
                        </div>
                    </label>

                    <label class="flex items-start gap-2.5 p-3 rounded-xl bg-gray-900 border border-amber-500/50 bg-amber-500/5 cursor-pointer">
                        <input type="radio" name="rehab-scope-tier" value="moderate" checked onchange="onTierChange('moderate')" class="accent-amber-500 mt-1">
                        <div>
                            <strong class="text-white block font-bold">שיפוץ בינוני (Moderate)</strong>
                            <span class="text-gray-400 text-[11px] block">מטבח חדש, אמבטיות, ריצוף וצבע ($55/sqft)</span>
                            <span class="text-emerald-400 font-mono font-bold mt-1 block">~$${(sqft * 55).toLocaleString()}</span>
                        </div>
                    </label>

                    <label class="flex items-start gap-2.5 p-3 rounded-xl bg-gray-900 border border-gray-800 cursor-pointer hover:border-amber-500/50">
                        <input type="radio" name="rehab-scope-tier" value="heavy" onchange="onTierChange('heavy')" class="accent-amber-500 mt-1">
                        <div>
                            <strong class="text-white block font-bold">שיפוץ מלא / שלד (Full Gut)</strong>
                            <span class="text-gray-400 text-[11px] block">פירוק מלא, גג, חשמל, צנרת ($100/sqft)</span>
                            <span class="text-emerald-400 font-mono font-bold mt-1 block">~$${(sqft * 100).toLocaleString()}</span>
                        </div>
                    </label>
                </div>
            </div>

            <!-- 2. Modular Line-Item Checklist -->
            <div class="bg-gray-950/80 p-4 rounded-xl border border-gray-800 space-y-2.5">
                <h4 class="text-xs font-black text-blue-400 uppercase tracking-wider flex items-center gap-1.5">
                    <i data-lucide="check-square" class="w-4 h-4"></i> 2. מפרט פריטים ומערכות להחלפה (Line-Item Builder):
                </h4>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-2.5 text-xs">
                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-kitchen" checked onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>מטבח חדש מלא + שיש</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$15,000</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-baths" checked onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>חדרי רחצה (${baths})</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$${(baths * 7500).toLocaleString()}</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-roof" onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>גג חדש (Asphalt Shingle)</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$9,500</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-hvac" onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>מערכת HVAC / חימום וקירור</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$9,000</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-electric" onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>לוח חשמל 200A וחיווט</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$4,500</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-plumbing" onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>צנרת ראשית (PEX Repipe)</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$6,000</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-flooring" checked onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>ריצוף LVP לכל הבית</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$${Math.round(sqft * 6.5).toLocaleString()}</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-paint" checked onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>צביעה פנימית מלאה</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$${Math.round(sqft * 3.0).toLocaleString()}</strong>
                    </label>

                    <label class="flex items-center justify-between p-2.5 rounded-lg bg-gray-900 border border-gray-800 cursor-pointer hover:border-blue-500/40">
                        <span class="flex items-center gap-2 text-gray-200">
                            <input type="checkbox" id="item-windows" onchange="recalculateRehab()" class="accent-blue-500 w-4 h-4">
                            <span>החלפת 8 חלונות כפולים</span>
                        </span>
                        <strong class="text-amber-300 font-mono">$5,200</strong>
                    </label>
                </div>
            </div>

            <!-- 3. Contractor Bid Sanity Check -->
            <div class="bg-gray-950/80 p-4 rounded-xl border border-purple-500/30 space-y-3">
                <h4 class="text-xs font-black text-purple-300 uppercase tracking-wider flex items-center gap-1.5">
                    <i data-lucide="scale" class="w-4 h-4"></i> 3. בקרת הצעת מחיר מקבלן (Contractor Sanity Check):
                </h4>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 items-center text-xs">
                    <div>
                        <label class="block font-bold text-gray-300 mb-1">הזן הצעת מחיר שקיבלת מקבלן ($):</label>
                        <input type="number" id="contractor-bid-input" placeholder="לדוגמה: 42000" oninput="checkContractorBid()" class="w-full bg-gray-900 border border-gray-700 rounded-lg px-3 py-2 text-sm font-bold text-white focus:border-purple-400">
                    </div>
                    <div id="contractor-verdict-box" class="bg-gray-900 p-3 rounded-xl border border-gray-800 text-center">
                        <span class="text-gray-400 block text-xs mb-0.5">חוות דעת על ההצעה:</span>
                        <strong class="text-gray-300 font-black text-sm" id="contractor-verdict-text">הזן סכום לבדיקה</strong>
                    </div>
                </div>
            </div>

            <!-- Footer Actions -->
            <div class="pt-3 border-t border-gray-800 flex justify-between items-center">
                <button onclick="saveRehabToDeal()" class="px-5 py-2.5 bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white rounded-xl text-xs font-black shadow-lg shadow-amber-600/20 flex items-center gap-1.5">
                    <i data-lucide="check" class="w-4 h-4"></i> שמור אומדן שיפוץ לעסקה
                </button>
                <button onclick="closeRehabModal()" class="px-4 py-2.5 bg-gray-800 hover:bg-gray-700 text-gray-300 rounded-xl text-xs font-bold">
                    סגור
                </button>
            </div>
        </div>
    </div>
    `;

    const existing = document.getElementById('rehab-modal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHTML);
    lucide.createIcons();
    recalculateRehab();
}

function onTierChange(tier) {
    const sqft = Number(currentRehabProperty?.sqft) || 1200;
    const baths = Number(currentRehabProperty?.baths) || 1;

    if (tier === 'light') {
        document.getElementById('item-kitchen').checked = false;
        document.getElementById('item-baths').checked = false;
        document.getElementById('item-roof').checked = false;
        document.getElementById('item-hvac').checked = false;
        document.getElementById('item-electric').checked = false;
        document.getElementById('item-plumbing').checked = false;
        document.getElementById('item-flooring').checked = true;
        document.getElementById('item-paint').checked = true;
        document.getElementById('item-windows').checked = false;
    } else if (tier === 'moderate') {
        document.getElementById('item-kitchen').checked = true;
        document.getElementById('item-baths').checked = true;
        document.getElementById('item-roof').checked = false;
        document.getElementById('item-hvac').checked = false;
        document.getElementById('item-electric').checked = false;
        document.getElementById('item-plumbing').checked = false;
        document.getElementById('item-flooring').checked = true;
        document.getElementById('item-paint').checked = true;
        document.getElementById('item-windows').checked = false;
    } else if (tier === 'heavy') {
        document.getElementById('item-kitchen').checked = true;
        document.getElementById('item-baths').checked = true;
        document.getElementById('item-roof').checked = true;
        document.getElementById('item-hvac').checked = true;
        document.getElementById('item-electric').checked = true;
        document.getElementById('item-plumbing').checked = true;
        document.getElementById('item-flooring').checked = true;
        document.getElementById('item-paint').checked = true;
        document.getElementById('item-windows').checked = true;
    }
    recalculateRehab();
}

function recalculateRehab() {
    if (!currentRehabProperty) return;

    const sqft = Number(currentRehabProperty.sqft) || 1200;
    const baths = Number(currentRehabProperty.baths) || 1;
    const askPrice = Number(currentRehabProperty.price) || 80000;
    const arv = Number(document.getElementById('rehab-arv-input')?.value) || Math.round(askPrice * 1.8);

    let itemsTotal = 0;
    if (document.getElementById('item-kitchen')?.checked) itemsTotal += 15000;
    if (document.getElementById('item-baths')?.checked) itemsTotal += (baths * 7500);
    if (document.getElementById('item-roof')?.checked) itemsTotal += 9500;
    if (document.getElementById('item-hvac')?.checked) itemsTotal += 9000;
    if (document.getElementById('item-electric')?.checked) itemsTotal += 4500;
    if (document.getElementById('item-plumbing')?.checked) itemsTotal += 6000;
    if (document.getElementById('item-flooring')?.checked) itemsTotal += Math.round(sqft * 6.5);
    if (document.getElementById('item-paint')?.checked) itemsTotal += Math.round(sqft * 3.0);
    if (document.getElementById('item-windows')?.checked) itemsTotal += 5200;

    // Add 15% Contingency
    const totalRehab = Math.round(itemsTotal * 1.15);

    // MAO = (ARV * 0.70) - Rehab
    const mao = Math.round((arv * 0.70) - totalRehab);
    const spread = mao - askPrice;

    const totalEl = document.getElementById('rehab-calc-total');
    if (totalEl) totalEl.textContent = '$' + totalRehab.toLocaleString();

    const maoEl = document.getElementById('rehab-calc-mao');
    if (maoEl) maoEl.textContent = '$' + mao.toLocaleString();

    const spreadEl = document.getElementById('rehab-calc-spread');
    if (spreadEl) {
        if (spread >= 0) {
            spreadEl.className = 'text-xl font-extrabold text-emerald-400';
            spreadEl.textContent = '+$' + spread.toLocaleString() + ' (רווח מומלץ)';
        } else {
            spreadEl.className = 'text-xl font-extrabold text-rose-400';
            spreadEl.textContent = '-$' + Math.abs(spread).toLocaleString() + ' (מחיר יקר)';
        }
    }

    checkContractorBid(totalRehab);
}

function checkContractorBid(calculatedRehab) {
    if (!calculatedRehab) {
        const text = document.getElementById('rehab-calc-total')?.textContent.replace(/[^0-9]/g, '');
        calculatedRehab = Number(text) || 35000;
    }

    const bidVal = Number(document.getElementById('contractor-bid-input')?.value);
    const box = document.getElementById('contractor-verdict-box');
    const text = document.getElementById('contractor-verdict-text');
    if (!box || !text) return;

    if (!bidVal || bidVal <= 0) {
        box.className = 'bg-gray-900 p-3 rounded-xl border border-gray-800 text-center';
        text.className = 'text-gray-300 font-black text-sm';
        text.textContent = 'הזן סכום לבדיקה';
        return;
    }

    const diffPercent = ((bidVal - calculatedRehab) / calculatedRehab) * 100;

    if (diffPercent < -25) {
        box.className = 'bg-rose-950/40 p-3 rounded-xl border border-rose-500/50 text-center';
        text.className = 'text-rose-400 font-black text-sm';
        text.textContent = `⚠️ מחיר זול מדי (${Math.round(diffPercent)}% מתחת למחירון). סכנת אי-סיום עבודה או תוספות.`;
    } else if (diffPercent >= -25 && diffPercent <= 15) {
        box.className = 'bg-emerald-950/40 p-3 rounded-xl border border-emerald-500/50 text-center';
        text.className = 'text-emerald-400 font-black text-sm';
        text.textContent = `🎯 מחיר מעולה וריאלי (${Math.round(diffPercent > 0 ? '+' : '')}${Math.round(diffPercent)}% מול המחירון).`;
    } else {
        box.className = 'bg-amber-950/40 p-3 rounded-xl border border-amber-500/50 text-center';
        text.className = 'text-amber-400 font-black text-sm';
        text.textContent = `📈 מחיר גבוה/מנופח (${Math.round(diffPercent)}%+ מעל המחירון). דרוש מו"מ עם הקבלן.`;
    }
}

function saveRehabToDeal() {
    if (!currentRehabProperty) return;
    const totalText = document.getElementById('rehab-calc-total')?.textContent || '$0';
    currentRehabProperty.rehab_scope = `אומדן שיפוץ שמור: ${totalText}`;
    
    // Save to localStorage
    const saved = localStorage.getItem('pa_pipeline_deals');
    if (saved) {
        let deals = JSON.parse(saved);
        const idx = deals.findIndex(d => String(d.id) === String(currentRehabProperty.id));
        if (idx !== -1) {
            deals[idx].rehab_scope = currentRehabProperty.rehab_scope;
            localStorage.setItem('pa_pipeline_deals', JSON.stringify(deals));
        }
    }

    alert(`אומדן השיפוץ בסך ${totalText} נשמר בהצלחה לנכס!`);
    closeRehabModal();
    if (typeof renderDeals === 'function') renderDeals();
}

function closeRehabModal() {
    const modal = document.getElementById('rehab-modal');
    if (modal) modal.remove();
}
