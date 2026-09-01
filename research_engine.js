// Research Engine - Deep Due Diligence & PDF Generator for PA Real Estate

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
    aiScore: 75,
    growthDriver: "כלכלה אזורית מעורבת ותעסוקה יציבה בפנסילבניה",
    permitStatus: "Standard Review",
    rentDemand: "ממוצע ויציב",
    medianRent: "$1,350",
    safetyGrade: "B",
    schoolsGrade: "7/10",
    marketTrend: "+3.8% שנתי"
};

let currentResearchProperty = null;

function executeDueDiligenceResearch(property) {
    currentResearchProperty = property;
    const county = property.county || "Luzerne";
    const macro = COUNTY_AI_MACRO_DATA[county] || DEFAULT_MACRO_DATA;
    const targetBuyer = property.tracker?.target_buyer || "משקיע / שותף";
    const askPrice = Number(property.price) || 0;
    const sqft = Number(property.sqft) || 1200;
    const estimatedRehab = Math.round(sqft * 45); // Standard estimate
    const estimatedArv = Math.round(askPrice * 1.75);

    // Save timestamp to deal if it's an existing deal
    if (property.id && !property.id.startsWith('custom_')) {
        property.last_researched_at = new Date().toLocaleDateString('he-IL');
        if (typeof savePipelineDeals === 'function') savePipelineDeals();
    }

    const modalHTML = `
    <div id="research-modal" class="fixed inset-0 bg-black/90 backdrop-blur-md z-50 flex items-center justify-center p-3 sm:p-5">
        <div class="bg-gray-900 border border-blue-500/40 rounded-2xl max-w-4xl w-full p-6 space-y-5 shadow-2xl relative max-h-[92vh] overflow-y-auto custom-scrollbar text-right">
            
            <!-- Modal Header with Print / Download Buttons -->
            <div class="flex justify-between items-start border-b border-gray-800 pb-3.5">
                <div>
                    <span class="px-2.5 py-0.5 rounded bg-blue-500/20 text-blue-300 border border-blue-500/30 text-xs font-black">
                        🔬 דוח מחקר מעמיק ובדיקת נאותות (Due Diligence Report)
                    </span>
                    <h2 class="text-2xl font-black text-white mt-1">${property.address}</h2>
                    <p class="text-xs text-gray-300">${property.city}, ${property.county} County, PA | <strong>מיועד עבור:</strong> ${targetBuyer}</p>
                </div>
                <div class="flex items-center gap-2">
                    <button onclick="downloadResearchPDF()" class="px-4 py-2 bg-gradient-to-r from-emerald-600 to-teal-600 hover:from-emerald-500 text-white rounded-xl text-xs font-black shadow-lg shadow-emerald-600/20 flex items-center gap-1.5 transition">
                        <i data-lucide="download" class="w-4 h-4"></i>
                        <span>הורד כ-PDF</span>
                    </button>
                    <button onclick="closeResearchModal()" class="text-gray-400 hover:text-white p-2 rounded-xl bg-gray-800"><i data-lucide="x" class="w-5 h-5"></i></button>
                </div>
            </div>

            <!-- Printable Report Container -->
            <div id="pdf-printable-area" class="space-y-4 p-4 bg-gray-950 rounded-xl border border-gray-800">
                
                <!-- Report Header Info -->
                <div class="flex justify-between items-center border-b border-gray-800 pb-3">
                    <div>
                        <h3 class="text-lg font-black text-white">${property.address}</h3>
                        <p class="text-xs text-gray-400">${property.city}, ${property.county} County, PA</p>
                    </div>
                    <div class="text-left">
                        <span class="text-xs text-gray-400 block">מיועד עבור:</span>
                        <strong class="text-blue-400 text-sm font-black">${targetBuyer}</strong>
                    </div>
                </div>

                <!-- 4 KPI Cards -->
                <div class="grid grid-cols-2 sm:grid-cols-4 gap-2.5 text-center">
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-[11px] text-gray-400 block mb-0.5">מדד תשתיות ו-AI</span>
                        <strong class="text-xl font-black text-emerald-400">${macro.aiScore} / 100</strong>
                    </div>
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-[11px] text-gray-400 block mb-0.5">מגמת מחירים בשכונה</span>
                        <strong class="text-base font-extrabold text-blue-400">${macro.marketTrend}</strong>
                    </div>
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-[11px] text-gray-400 block mb-0.5">מדד ביטחון ופשיעה</span>
                        <strong class="text-base font-extrabold text-amber-300">${macro.safetyGrade}</strong>
                    </div>
                    <div class="bg-gray-900 p-3 rounded-lg border border-gray-800">
                        <span class="text-[11px] text-gray-400 block mb-0.5">דירוג בתי ספר</span>
                        <strong class="text-base font-extrabold text-white">${macro.schoolsGrade}</strong>
                    </div>
                </div>

                <!-- AI, Data Centers & Energy Drivers -->
                <div class="bg-gray-900 p-3.5 rounded-lg border border-blue-500/20 space-y-2 text-xs">
                    <h4 class="font-black text-blue-400 flex items-center gap-1.5 uppercase">
                        <i data-lucide="zap" class="w-4 h-4 text-amber-300"></i> ניתוח השפעת משרות, AI ותשתיות אנרגיה במחוז:
                    </h4>
                    <div class="grid grid-cols-1 sm:grid-cols-2 gap-2">
                        <div class="bg-gray-950 p-2.5 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">מנוע צמיחה והשקעות עוגן:</span>
                            <strong class="text-white text-sm font-extrabold">${macro.growthDriver}</strong>
                        </div>
                        <div class="bg-gray-950 p-2.5 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">סטטוס אישורי רשת וחשמל:</span>
                            <strong class="text-emerald-300 text-sm font-extrabold">${macro.permitStatus}</strong>
                        </div>
                    </div>
                </div>

                <!-- Rental Market & Demographics -->
                <div class="bg-gray-900 p-3.5 rounded-lg border border-gray-800 space-y-2 text-xs">
                    <h4 class="font-black text-amber-300 flex items-center gap-1.5 uppercase">
                        <i data-lucide="trending-up" class="w-4 h-4 text-emerald-400"></i> מדדי שכירות וביקוש מקומי:
                    </h4>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">שכירות חציונית</span>
                            <strong class="text-white text-sm font-black">${macro.medianRent} / חודש</strong>
                        </div>
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">ביקוש שוכרים</span>
                            <strong class="text-emerald-300 text-sm font-black">${macro.rentDemand}</strong>
                        </div>
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">ימי שוק ממוצעים</span>
                            <strong class="text-white text-sm font-black">34 ימים</strong>
                        </div>
                    </div>
                </div>

                <!-- Financial Underwriting Summary -->
                <div class="bg-gray-900 p-3.5 rounded-lg border border-emerald-500/20 space-y-2 text-xs">
                    <h4 class="font-black text-emerald-400 flex items-center gap-1.5 uppercase">
                        <i data-lucide="calculator" class="w-4 h-4"></i> סיכום פיננסי ראשוני:
                    </h4>
                    <div class="grid grid-cols-3 gap-2">
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">מחיר מבוקש</span>
                            <strong class="text-white text-sm font-black">$${askPrice.toLocaleString()}</strong>
                        </div>
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">אומדן שיפוץ משוער</span>
                            <strong class="text-amber-300 text-sm font-black">$${estimatedRehab.toLocaleString()}</strong>
                        </div>
                        <div class="bg-gray-950 p-2 rounded border border-gray-800">
                            <span class="text-gray-400 block font-bold">שווי לאחר שיפוץ (ARV)</span>
                            <strong class="text-emerald-400 text-sm font-black">$${estimatedArv.toLocaleString()}</strong>
                        </div>
                    </div>
                </div>

                <div class="text-[10px] text-gray-500 text-left pt-1 font-mono">
                    PA Real Estate Intelligence Hub • Automated Underwriting Report • Date: ${new Date().toLocaleDateString('he-IL')}
                </div>
            </div>

            <!-- External Verification Quick Links -->
            <div class="pt-2 border-t border-gray-800 flex flex-wrap justify-between items-center gap-2">
                <div class="flex flex-wrap gap-2">
                    <a href="https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(property.address + ', ' + property.city + ', PA')}" target="_blank" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                        <i data-lucide="map" class="w-3.5 h-3.5 text-emerald-400"></i> מפת רחוב
                    </a>
                    <a href="https://www.greatschools.org/search/search.page?q=${encodeURIComponent(property.city + ' ' + (property.zip || ''))}" target="_blank" class="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-xl text-xs font-bold flex items-center gap-1.5 transition">
                        <i data-lucide="graduation-cap" class="w-3.5 h-3.5 text-blue-400"></i> בתי ספר (GreatSchools)
                    </a>
                </div>
                <button onclick="closeResearchModal()" class="px-5 py-2 bg-blue-600 hover:bg-blue-500 text-white rounded-xl text-xs font-black transition">
                    סגור
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

function downloadResearchPDF() {
    const element = document.getElementById('pdf-printable-area');
    if (!element) return;

    const opt = {
        margin:       10,
        filename:     `PA_Research_${(currentResearchProperty?.address || 'Report').replace(/[^a-zA-Z0-9]/g, '_')}.pdf`,
        image:        { type: 'jpeg', quality: 0.98 },
        html2canvas:  { scale: 2, useCORS: true },
        jsPDF:        { unit: 'mm', format: 'a4', orientation: 'portrait' }
    };

    html2pdf().set(opt).from(element).save();
}

function closeResearchModal() {
    const modal = document.getElementById('research-modal');
    if (modal) modal.remove();
}
