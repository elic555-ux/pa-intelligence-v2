// Research Engine - Deep Due Diligence Module for PA Real Estate

const COUNTY_AI_MACRO_DATA = {
    "Luzerne": {
        tier: "Tier A",
        aiScore: 95,
        growthDriver: "מתחם חוות שרתים ענק של אמזון ($20B) ליד תחנת הכוח Susquehanna",
        permitStatus: "Permitted & Powered",
        rentDemand: "גבוה מאוד (ביקוש עובדי תשתיות ובנייה)",
        medianRent: "$1,550",
        safetyGrade: "B+",
        schoolsGrade: "7/10",
        marketTrend: "+6.8% שנתי"
    },
    "Bucks": {
        tier: "Tier A",
        aiScore: 92,
        growthDriver: "קמפוס AI של אמזון ב-Falls Township",
        permitStatus: "Permitted & Under Construction",
        rentDemand: "ביקוש גבוה מאוד (שוק יוקרתי)",
        medianRent: "$2,400",
        safetyGrade: "A",
        schoolsGrade: "9/10",
        marketTrend: "+5.2% שנתי"
    },
    "Allegheny": {
        tier: "Tier A",
        aiScore: 90,
        growthDriver: "אקוסיסטם רובוטיקה, אוניברסיטאות ומחקר AI (פיטסבורג)",
        permitStatus: "High-Tech Growth Hub",
        rentDemand: "יציב וחיובי",
        medianRent: "$1,450",
        safetyGrade: "B",
        schoolsGrade: "8/10",
        marketTrend: "+4.5% שנתי"
    },
    "Northampton": {
        tier: "Tier A",
        aiScore: 88,
        growthDriver: "מסדרון לוגיסטיקה, Data Centers וקרבה ל-NY/NJ",
        permitStatus: "Permitted & Active",
        rentDemand: "גבוה",
        medianRent: "$1,850",
        safetyGrade: "B+",
        schoolsGrade: "8/10",
        marketTrend: "+5.9% שנתי"
    },
    "Lehigh": {
        tier: "Tier A",
        aiScore: 87,
        growthDriver: "ייצור מתקדם, לוגיסטיקה ובריאות",
        permitStatus: "Active Economic Hub",
        rentDemand: "גבוה ויציב",
        medianRent: "$1,950",
        safetyGrade: "B+",
        schoolsGrade: "7.5/10",
        marketTrend: "+5.4% שנתי"
    },
    "Washington": {
        tier: "Tier B",
        aiScore: 85,
        growthDriver: "אנרגיה וגז טבעי לכוח Data Centers (פרויקט Alpha Compute)",
        permitStatus: "Permitted & Powered",
        rentDemand: "צומח",
        medianRent: "$1,380",
        safetyGrade: "B+",
        schoolsGrade: "7/10",
        marketTrend: "+4.8% שנתי"
    },
    "Dauphin": {
        tier: "Tier B",
        aiScore: 80,
        growthDriver: "מרכז ממשלתי, לוגיסטיקה ובריאות (האריסברג)",
        permitStatus: "Stable Core",
        rentDemand: "יציב (תזרים חזק)",
        medianRent: "$1,420",
        safetyGrade: "B-",
        schoolsGrade: "6.5/10",
        marketTrend: "+4.1% שנתי"
    },
    "Berks": {
        tier: "Tier B",
        aiScore: 78,
        growthDriver: "תעשייה ולוגיסטיקה סביב רדינג",
        permitStatus: "Industrial Inflow",
        rentDemand: "ביקוש יציב לתזרים (Cash Flow)",
        medianRent: "$1,480",
        safetyGrade: "B-",
        schoolsGrade: "6.5/10",
        marketTrend: "+4.0% שנתי"
    }
};

const DEFAULT_MACRO_DATA = {
    tier: "Tier B",
    aiScore: 72,
    growthDriver: "כלכלה אזורית מעורבת ותעסוקה יציבה",
    permitStatus: "Standard Review",
    rentDemand: "ממוצע ויציב",
    medianRent: "$1,350",
    safetyGrade: "B",
    schoolsGrade: "7/10",
    marketTrend: "+3.8% שנתי"
};

function executeDueDiligenceResearch(property) {
    const county = property.county || "Allegheny";
    const macro = COUNTY_AI_MACRO_DATA[county] || DEFAULT_MACRO_DATA;

    const modalHTML = `
    <div id="research-modal" class="fixed inset-0 bg-black/90 backdrop-blur-md z-50 flex items-center justify-center p-3 sm:p-5">
        <div class="bg-gray-900 border border-blue-500/40 rounded-2xl max-w-4xl w-full p-6 space-y-5 shadow-2xl relative max-h-[92vh] overflow-y-auto custom-scrollbar text-right">
            
            <!-- Header -->
            <div class="flex justify-between items-start border-b border-gray-800 pb-3.5">
                <div>
                    <div class="flex items-center gap-2 mb-1.5">
                        <span class="px-2.5 py-0.5 rounded bg-blue-500/20 text-blue-400 border border-blue-500/30 text-xs font-black">
                            🔬 דוח בדיקת נאותות ומחקר סביבתי מעמיק
                        </span>
                        <span class="px-2.5 py-0.5 rounded bg-amber-500/20 text-amber-300 border border-amber-500/30 text-xs font-black">
                            ${macro.tier}
                        </span>
                    </div>
                    <h2 class="text-2xl font-black text-white">${property.address}</h2>
                    <p class="text-xs text-gray-400 mt-1">${property.city}, ${property.county} County, PA ${property.zip || ''}</p>
                </div>
                <button onclick="closeResearchModal()" class="text-gray-400 hover:text-white p-2 rounded-xl bg-gray-800"><i data-lucide="x" class="w-5 h-5"></i></button>
            </div>

            <!-- Score Summary Cards -->
            <div class="grid grid-cols-2 sm:grid-cols-4 gap-3 text-center">
                <div class="bg-gray-950 p-3.5 rounded-xl border border-gray-800">
                    <span class="text-xs text-gray-400 block mb-1">מדד צמיחת משרות ו-AI</span>
                    <strong class="text-2xl font-black text-emerald-400">${macro.aiScore} / 100</strong>
                </div>
                <div class="bg-gray-950 p-3.5 rounded-xl border border-gray-800">
                    <span class="text-xs text-gray-400 block mb-1">מגמת מחירים בשכונה</span>
                    <strong class="text-xl font-extrabold text-blue-400">${macro.marketTrend}</strong>
                </div>
                <div class="bg-gray-950 p-3.5 rounded-xl border border-gray-800">
                    <span class="text-xs text-gray-400 block mb-1">מדד ביטחון ופשיעה</span>
                    <strong class="text-xl font-extrabold text-amber-300">${macro.safetyGrade}</strong>
                </div>
                <div class="bg-gray-950 p-3.5 rounded-xl border border-gray-800">
                    <span class="text-xs text-gray-400 block mb-1">דירוג בתי ספר</span>
                    <strong class="text-xl font-extrabold text-white">${macro.schoolsGrade}</strong>
                </div>
            </div>

            <!-- AI, Power & Infrastructure Analysis -->
            <div class="bg-gray-950/80 p-4 rounded-xl border border-blue-500/20 space-y-2.5">
                <h4 class="text-xs font-black text-blue-400 uppercase tracking-wider flex items-center gap-1.5">
                    <i data-lucide="zap" class="w-4 h-4 text-amber-300"></i> ניתוח השפעת AI, תשתיות אנרגיה ותעסוקה מקומית:
                </h4>
                <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-gray-400 block font-bold mb-0.5">מנוע צמיחה אזורי / השקעות עוגן:</span>
                        <strong class="text-white font-extrabold text-sm">${macro.growthDriver}</strong>
                    </div>
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-gray-400 block font-bold mb-0.5">סטטוס אישורי חשמל ורגולציה:</span>
                        <strong class="text-emerald-300 font-extrabold text-sm">${macro.permitStatus}</strong>
                    </div>
                </div>
            </div>

            <!-- Rental & Market Demographics -->
            <div class="bg-gray-950/80 p-4 rounded-xl border border-gray-800 space-y-2.5 text-xs">
                <h4 class="text-xs font-black text-amber-400 uppercase tracking-wider flex items-center gap-1.5">
                    <i data-lucide="trending-up" class="w-4 h-4 text-emerald-400"></i> מדדי שכירות וביקוש שוכרים:
                </h4>
                <div class="grid grid-cols-2 sm:grid-cols-3 gap-3">
                    <div class="bg-gray-900 p-2.5 rounded-lg border border-gray-800">
                        <span class="text-gray-400 block font-bold">שכירות חציונית בסביבה</span>
                        <strong class="text-white font-black text-sm">${macro.medianRent} / חודש</strong>
                    </div>
                    <div class="bg-gray-900 p-2.5 rounded-lg border border-gray-800">
                        <span class="text-gray-400 block font-bold">עוצמת ביקושי שכירות</span>
                        <strong class="text-emerald-300 font-black text-sm">${macro.rentDemand}</strong>
                    </div>
                    <div class="bg-gray-900 p-2.5 rounded-lg border border-gray-800">
                        <span class="text-gray-400 block font-bold">ימי שוק ממוצעים (DOM)</span>
                        <strong class="text-white font-black text-sm">34 ימים</strong>
                    </div>
                </div>
            </div>

            <!-- External Verification Links -->
            <div class="pt-3 border-t border-gray-800 flex flex-wrap justify-between items-center gap-2.5">
                <div class="flex flex-wrap gap-2">
                    <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(property.address + ', ' + property.city + ', PA')}" target="_blank" class="px-3.5 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                        <i data-lucide="map" class="w-3.5 h-3.5 text-emerald-400"></i> מפת רחוב
                    </a>
                    <a href="https://www.greatschools.org/search/search.page?q=${encodeURIComponent(property.city + ' ' + property.zip)}" target="_blank" class="px-3.5 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                        <i data-lucide="graduation-cap" class="w-3.5 h-3.5 text-blue-400"></i> בתי ספר (GreatSchools)
                    </a>
                    <a href="https://www.neighborhoodscout.com/pa/${property.city.toLowerCase()}/crime" target="_blank" class="px-3.5 py-2 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                        <i data-lucide="shield" class="w-3.5 h-3.5 text-amber-400"></i> מפת פשיעה (CrimeScout)
                    </a>
                </div>
                <button onclick="closeResearchModal()" class="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-black transition">
                    סגור דוח
                </button>
            </div>
        </div>
    </div>
    `;

    const existing = document.getElementById('research-modal');
    if (existing) existing.remove();

    document.body.insertAdjacentHTML('beforeend', modalHTML);
    lucide.createIcons();
}

function closeResearchModal() {
    const modal = document.getElementById('research-modal');
    if (modal) modal.remove();
}
