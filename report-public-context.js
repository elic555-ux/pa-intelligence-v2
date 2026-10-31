(function(root,factory){'use strict';if(typeof module==='object'&&module.exports)module.exports=factory();else root.PAReportPublicContext=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const identity=p=>[String(p?.id||''),String(p?.address||'').trim().toLowerCase(),String(p?.city||'').trim().toLowerCase(),String(p?.state||'PA').toUpperCase(),String(p?.zip||'').slice(0,5)];
  function link(value,hosts){try{const u=new URL(value);return u.protocol==='https:'&&!u.username&&!u.password&&hosts.includes(u.hostname)?u.href:null;}catch(_){return null;}}
  function valid(c,p,county,now=Date.now()){
    const time=Date.parse(c?.generated_at);
    return c?.version==='official-public-context-1'&&c.property_id===String(p.id)&&JSON.stringify(identity(c.property))===JSON.stringify(identity(p))&&
      /^[A-Z0-9]{16}$/.test(c.parcel_id||'')&&(!county?.parcel_id||county.parcel_id===c.parcel_id)&&
      Number.isFinite(time)&&time<=now&&now-time<=100*86400000;
  }
  const source=(name,when,url)=>'<p class="source-note">'+esc(name)+' · '+esc(when?String(when).slice(0,10):'מועד נתונים לא זמין')+(url?' · <a target="_blank" rel="noopener noreferrer" href="'+esc(url)+'">מקור רשמי</a>':'')+'</p>';
  function render(c,p,county,now=Date.now()){
    if(!valid(c,p,county,now))return '<p class="missing">בתי ספר ופשיעה: טרם התקבל מידע רשמי התואם לנכס.</p>';
    const edu=c.education||{},url=link(edu.source_url,['www.futurereadypa.org','futurereadypa.org']);
    let out='<h3>בתי ספר — מחוז החינוך של החלקה</h3>';
    const district=edu.district_match_basis==='county_parcel_school_district'&&edu.parcel_id===c.parcel_id&&
      link(edu.district_source_url,['data.wprdc.org'])?edu.district:null;
    if(district){
      out+='<p>'+esc(district)+' · '+'מהדורת המקור: '+esc(edu.source_period||'לא זמינה')+'</p>';
      const schools=Array.isArray(edu.schools)?edu.schools:[];
      const rows=schools.flatMap(s=>(Array.isArray(s.metrics)?s.metrics:[]).filter(m=>m.unit==='percent').map(m=>({school:s.school_name,...m})));
      const observed=Date.parse(edu.observed_at),fresh=Number.isFinite(observed)&&observed<=now&&now-observed<=550*86400000;
      if(rows.length&&url&&fresh){
        if(edu.status==='cached_stale')out+='<p class="missing">עדכון נתוני החינוך לא הושלם; מוצגת גרסה קודמת.</p>';
        out+='<details><summary>ביצועים שפורסמו · '+esc(schools.length)+' בתי ספר במחוז</summary><div class="table-scroll"><table><thead><tr><th>בית ספר</th><th>מדד משרד החינוך</th><th>ביצוע</th></tr></thead><tbody>'+rows.map(m=>{
          const value=typeof m.value==='number'&&Number.isFinite(m.value)&&m.value>=0&&m.value<=100?m.value.toFixed(1)+'%':'לא פורסם';
          return '<tr><td>'+esc(m.school)+'</td><td>'+esc(m.label)+'</td><td>'+esc(value)+'</td></tr>';
        }).join('')+'</tbody></table></div></details>'+source(edu.source_name,edu.observed_at,url);
      }else out+='<p class="missing">נתוני הביצועים של מחוז החינוך עדיין אינם זמינים.</p>';
      out+='<p class="source-note">ההתאמה היא למחוז חינוך לפי רישום החלקה; אינה אישור לשיבוץ בבית ספר מסוים. שנת המדידה עשויה להשתנות בין מדדים.</p>';
    }else out+='<p class="missing">מחוז החינוך של החלקה טרם אומת.</p>';
    const crime=c.crime||{},crimeUrl=link(crime.source_url,['www.ucr.pa.gov','ucr.pa.gov']);
    out+='<h3>פשיעה — מקור רשמי</h3><p class="missing">לא התקבלו נתוני פשיעה עדכניים ומאומתים עבור סביבת הנכס.</p>'+
      (crimeUrl?'<p class="source-note">'+esc(c.municipality||'הרשות המקומית טרם אומתה')+' · <a target="_blank" rel="noopener noreferrer" href="'+esc(crimeUrl)+'">בדיקה במערכת משטרת פנסילבניה</a></p>':'');
    return out;
  }
  return {valid,render};
});
