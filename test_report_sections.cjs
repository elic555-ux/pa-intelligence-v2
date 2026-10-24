'use strict';
const assert=require('node:assert/strict'),fs=require('node:fs');
const R=require('./report-sections.js');
class El{
  constructor(doc,tag='div',id=''){this.ownerDocument=doc;this.tagName=tag.toUpperCase();this.attributes={};this.children=[];this.parentElement=null;this._text='';this.value='';this.listeners={};if(id)this.id=id;
    this.classList={contains:s=>this.className.split(/\s+/).includes(s),add:(...names)=>{this.className=[...new Set([...this.className.split(/\s+/).filter(Boolean),...names])].join(' ');}};}
  get id(){return this.attributes.id||'';}set id(v){this.attributes.id=v;}
  get className(){return this.attributes.class||'';}set className(v){this.attributes.class=v;}
  set textContent(v){this._text=String(v);this.children.forEach(n=>n.parentElement=null);this.children=[];}
  get textContent(){return this._text+this.children.map(n=>n.textContent).join('');}
  setAttribute(k,v){this.attributes[k]=String(v);}getAttribute(k){return this.attributes[k]??null;}
  get firstChild(){return this.children[0]||null;}
  detach(){if(this.parentElement){const p=this.parentElement;p.children.splice(p.children.indexOf(this),1);this.parentElement=null;}}
  appendChild(node){node.detach();node.parentElement=this;this.children.push(node);return node;}
  append(...nodes){nodes.forEach(n=>this.appendChild(n));}
  insertBefore(node,reference){if(node===reference)return node;node.detach();const i=reference?this.children.indexOf(reference):this.children.length;if(i<0)throw Error('Reference is not a child');node.parentElement=this;this.children.splice(i,0,node);return node;}
  after(node){if(node===this)return;const p=this.parentElement;node.detach();node.parentElement=p;p.children.splice(p.children.indexOf(this)+1,0,node);}
  replaceChildren(...nodes){this.children.forEach(n=>n.parentElement=null);this.children=[];this._text='';this.append(...nodes);}
  contains(node){return this===node||this.children.some(n=>n.contains(node));}
  matches(selector){if(selector.startsWith('.'))return this.classList.contains(selector.slice(1));const attr=selector.match(/^\[([^=\]]+)(?:="([^"]*)")?\]$/);if(attr)return attr[2]===undefined?attr[1] in this.attributes:this.getAttribute(attr[1])===attr[2];return this.tagName===selector.toUpperCase();}
  all(){return this.children.flatMap(n=>[n,...n.all()]);}
  querySelector(selector){const parts=selector.split(' ');for(const node of this.all()){if(!node.matches(parts.at(-1)))continue;if(parts.length===1)return node;let parent=node.parentElement;while(parent&&parent!==this){if(parent.matches(parts[0]))return node;parent=parent.parentElement;}}return null;}
  closest(selector){for(let n=this;n;n=n.parentElement)if(n.matches(selector))return n;return null;}
  addEventListener(name,fn){this.listeners[name]=fn;}
  scrollIntoView(){this.scrolled=(this.scrolled||0)+1;}
  focus(){this.focused=true;}
}
function fixture(){
  const doc={createElement(tag){return new El(doc,tag);},getElementById(id){return [root,...root.all()].find(n=>n.id===id)||null;}};
  const root=new El(doc,'main','report-content');
  const add=(parent,tag,id,cls,text)=>{const n=new El(doc,tag,id);n.className=cls||'';if(text)n.textContent=text;parent.appendChild(n);return n;};
  const nav=add(root,'nav','report-sections-nav');
  const overview=add(root,'div','tab-overview'),financial=add(root,'div','tab-financials'),rehabTab=add(root,'div','tab-rehab');
  const property=add(overview,'div','report-section-property'),photos=add(property,'div','report-section-photos');
  add(photos,'span','','report-gallery-caption','Photos');const image=add(photos,'img','real-image');image.onload=()=>{};
  const grid=add(property,'div','','report-value-grid');add(grid,'div','asking');add(grid,'div','market');add(grid,'div','arv');
  const summary=add(overview,'div');add(summary,'p','rep-summary','','Observed source summary');
  add(overview,'section','investment-scenario-overview');const history=add(overview,'section','report-section-history');add(history,'h2','','','History');
  const availability=add(overview,'section');add(availability,'h2','report-data-title','','Missing source evidence');
  const area=add(overview,'div','report-section-neighborhood');add(area,'h4','','','Area');const county=add(overview,'section','internal-data-card');add(county,'h2','','','County');
  const scenario=add(financial,'section','deal-analysis-root'),da=add(scenario,'div','','da-heading');add(da,'h2','','','Calculator');
  const input=add(scenario,'input','scenario-input');input.value='110000';input.listenerMarker={};
  const value=add(financial,'section','report-section-value');add(value,'h2','','','Sales comparisons');
  const rent=add(financial,'section','report-section-rent');add(rent,'h2','','','Rental comparisons');
  const sources=add(financial,'section','report-section-sources');add(sources,'h2','','','Sources');
  for(const id of ['evidence-editor','target-editor','source-settings'])add(financial,'section',id);
  const rehab=add(rehabTab,'section','report-section-rehab');add(rehab,'div','rehab-report-root');const quote=add(rehabTab,'section');add(quote,'div','contractor-quotes');add(rehabTab,'section','inspection-editor');
  return {root,doc,nav,input,image,property,photos,overview,financial,area,county,value,rent,scenario,sources,rehab};
}
const tests=[];const test=(name,fn)=>tests.push([name,fn]);
const finance={rent:1820,evidence:{rent:{source_name:'RentCast',observed_at:'2026-10-22T19:06:25Z'}}};
test('Ten immutable sections have unique IDs, numbers and supported tabs',()=>{
  assert.equal(R.SECTIONS.length,10);assert.deepEqual(R.SECTIONS.map(s=>s.number),Array.from({length:10},(_,i)=>String(i+1).padStart(2,'0')));
  for(const field of ['id','target'])assert.equal(new Set(R.SECTIONS.map(s=>s[field])).size,10);
  assert.equal(Object.isFrozen(R.SECTIONS),true);assert.ok(R.SECTIONS.every(s=>Object.isFrozen(s)&&['tab-overview','tab-financials','tab-rehab'].includes(s.tab)));
});
test('Every catalog destination exists exactly once in the actual report HTML',()=>{
  const html=fs.readFileSync(__dirname+'/report.html','utf8');for(const s of R.SECTIONS)assert.equal((html.match(new RegExp('id="'+s.target+'"','g'))||[]).length,1,s.id);
  assert.match(html,/report-sections\.js\?v=8\.24\.1/);assert.match(html,/window\.PAReportSections\.mount/);
});
test('Mount creates ten accessible headings and ten direct links',()=>{
  const h=fixture();R.mount(h.root,{finance});assert.equal(h.root.all().filter(n=>n.getAttribute('data-rn-heading')).length,10);assert.equal(h.nav.all().filter(n=>n.getAttribute('data-rn-link')).length,10);
  for(const s of R.SECTIONS){const target=h.doc.getElementById(s.target);assert.equal(target.getAttribute('data-report-number'),s.number);assert.match(target.querySelector('[data-rn-heading="'+s.id+'"]').textContent,new RegExp(s.number));}
});
test('Organizing moves the real image, input and source nodes without replacing their state',()=>{
  const h=fixture(),onload=h.image.onload,marker=h.input.listenerMarker;R.mount(h.root,{finance});
  assert.equal(h.doc.getElementById('real-image'),h.image);assert.equal(h.image.onload,onload);assert.equal(h.doc.getElementById('scenario-input'),h.input);assert.equal(h.input.value,'110000');assert.equal(h.input.listenerMarker,marker);
  assert.equal(h.doc.getElementById('market').parentElement.parentElement,h.value);assert.equal(h.doc.getElementById('arv').parentElement.parentElement,h.value);assert.ok(h.property.contains(h.doc.getElementById('asking')));
});
test('Overview order is property, photos, county, area and price history',()=>{
  const h=fixture();R.mount(h.root,{finance});assert.deepEqual(h.overview.children.map(n=>n.id),['report-section-property','report-section-photos','internal-data-card','report-section-neighborhood','report-section-history']);
  assert.deepEqual(h.financial.children.slice(0,3).map(n=>n.id),['report-section-value','report-section-rent','deal-analysis-root']);
});
test('Repeated refresh does not duplicate section headings, source cards, links or media',()=>{
  const h=fixture();for(let i=0;i<5;i++)R.mount(h.root,{finance});assert.equal(h.nav.all().filter(n=>n.getAttribute('data-rn-link')).length,10);assert.equal(h.root.all().filter(n=>n.getAttribute('data-rn-heading')).length,10);assert.equal(h.root.all().filter(n=>n.tagName==='IMG').length,1);assert.equal(h.root.all().filter(n=>n.id==='arv').length,1);
});
test('New rent data refreshes the source summary without resetting a draft offer',()=>{
  const h=fixture();R.mount(h.root,{finance});assert.match(h.rent.querySelector('[data-rn-rent]').textContent,/1,820/);h.input.value='99000';R.mount(h.root,{finance:{rent:1900,evidence:{}}});assert.match(h.rent.querySelector('[data-rn-rent]').textContent,/1,900/);assert.equal(h.input.value,'99000');
});
test('Missing rental evidence stays visibly missing and source names are plain text',()=>{
  const h=fixture();R.mount(h.root,{});assert.match(h.rent.querySelector('[data-rn-rent]').textContent,/טרם התקבל/);R.mount(h.root,{finance:{rent:1820,evidence:{rent:{source_name:'<img onerror=x>',observed_at:'2026-10-22'}}}});assert.match(h.rent.querySelector('[data-rn-rent]').textContent,/<img onerror=x>/);assert.equal(h.root.all().filter(n=>n.tagName==='IMG').length,1);
});
test('Section navigation opens its correct tab, scrolls and focuses the heading',()=>{
  const h=fixture(),calls=[],locations=[];const ctrl=R.mount(h.root,{finance,onNavigate:tab=>calls.push(tab),onLocation:id=>locations.push(id)});
  assert.equal(ctrl.navigate('rehab'),true);assert.deepEqual(calls,['tab-rehab']);assert.deepEqual(locations,['report-section-rehab']);assert.equal(h.rehab.scrolled,1);assert.equal(h.rehab.querySelector('[data-rn-heading="rehab"]').focused,true);
  assert.equal(ctrl.navigate('javascript:bad'),false);assert.equal(calls.length,1);
});
test('Opening a numbered anchor navigates once and refresh does not steal focus',()=>{
  const h=fixture(),calls=[];R.mount(h.root,{hash:'#report-section-value',onNavigate:t=>calls.push(t)});R.mount(h.root,{hash:'#report-section-value',onNavigate:t=>calls.push(t)});assert.deepEqual(calls,['tab-financials']);
});
test('Modified link clicks remain available and ordinary nested clicks navigate',()=>{
  const h=fixture(),calls=[];R.mount(h.root,{onNavigate:t=>calls.push(t)});const a=h.nav.all().find(n=>n.getAttribute('data-rn-link')==='rent');let prevented=0;
  h.nav.listeners.click({target:a.children[0],ctrlKey:true,preventDefault(){prevented++;}});assert.equal(prevented,0);
  h.nav.listeners.click({target:a.children[0],preventDefault(){prevented++;}});assert.equal(prevented,1);assert.deepEqual(calls,['tab-financials']);
});
test('The section layer has no provider, save or export commands',()=>{
  const code=fs.readFileSync(__dirname+'/report-sections.js','utf8');assert.ok(!/fetch\(|supabase[^;]*\.from\(|localStorage|workflow_dispatch|html2pdf/.test(code));
  const h=fixture();R.mount(h.root,{finance});assert.ok(h.sources.contains(h.doc.getElementById('report-data-title')));assert.ok(h.rehab.contains(h.doc.getElementById('contractor-quotes')));assert.ok(h.rehab.contains(h.doc.getElementById('inspection-editor')));
});
let passed=0;for(const [name,fn]of tests){try{fn();passed++;console.log('PASS '+name);}catch(e){process.exitCode=1;console.error('FAIL '+name,e);}}console.log(passed+'/'+tests.length+' numbered report checks passed.');
