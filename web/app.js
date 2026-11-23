const $ = id => document.getElementById(id);
let mode = "search", scope = null, version = 0, detailVersion = 0, stats;
const node = (tag, text, cls) => {
  const item = document.createElement(tag);
  if (text !== undefined) item.textContent = text;
  if (cls) item.className = cls;
  return item;
};
const action = (label, fn, cls = "secondary") => {
  const btn = node("button", label, cls);
  btn.type = "button"; btn.onclick = fn; return btn;
};
const link = (label, href) => {
  const a = node("a", label); a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer"; return a;
};
async function api(route, params = {}) {
  const res = await fetch("/api/" + route + "?" + new URLSearchParams(params));
  if (!res.ok) throw Error("Unable to open this source. Try again.");
  return res.json();
}
function fail(err) { $("status").textContent = err.message || "Request failed."; }
function clearDetail() { detailVersion++; $("detail").replaceChildren(); }
function resetView(next) {
  mode = next; version++; clearDetail(); scope = null;
  $("query").value = ""; $("output").replaceChildren(); $("scope").replaceChildren();
  $("nav-search").removeAttribute("aria-current"); $("nav-library").removeAttribute("aria-current");
  $(next === "search" ? "nav-search" : "nav-library").setAttribute("aria-current","page");
  const lib = next === "library";
  $("title").textContent = $("crumb").textContent = lib ? "Source library" : "Specification search";
  $("intro").textContent = lib ? "Browse the documents in this workspace and inspect their indexed text." : "Look up a product, compare glazing options, or trace a value to its source.";
  $("query").placeholder = lib ? "Filter documents by product, manufacturer or title" : "Ask about a model, assembly or performance value";
  $("submit").textContent = lib ? "Filter" : "Search";
  $("shortcuts").replaceChildren();
  if (lib) return listing(0).catch(fail);
  const queries = stats.demo ? [["Venting U-factor","What is the U-factor for AFV/20?"],["Fixed glazing","AFF/10 U-factor"]] :
    [["VELUX venting U-factor","What is the U-factor for the VELUX VMS venting skylight?"],["CENTRIA wall panels","CENTRIA Formawall wall panel"],["Swisspearl installation","Swisspearl installation fasteners"]];
  for (const [label, query] of queries) $("shortcuts").append(action(label, () => { $("query").value=query; run().catch(fail); }, "chip"));
  $("status").textContent = stats.searchable + " searchable sources · " + stats.pages.toLocaleString() + " PDF pages";
  $("output").append(node("h2","Browse source documents"));
  const current = version;
  const sample=stats.demo ? ["Demo fixed","Demo venting"] : ["VELUX VMS.pdf","Formawall-Tech-Data-Sheet","Kalwall Corporation"];
  Promise.all(sample.map(q=>api("documents",{q}))).then(groups=>{
    if (current !== version) return;
    for (const g of groups) if(g.items.length) $("output").append(docButton(g.items[0]));
  }).catch(fail);
}
function docButton(d) {
  const btn=action("",()=>inspect(d.id).catch(fail),"doc");
  btn.append(node("span",d.name),node("small",d.state==="metadata only"?"Metadata only":(d.pdf ? d.pages+" pp. · PDF" : d.kind==="url"?"Web source":"Indexed text")));return btn;
}
async function listing(offset) {
  const id=++version;clearDetail();
  const result=await api("documents",{q:$("query").value.trim(),offset});
  if(id!==version)return;
  $("output").replaceChildren(); $("status").textContent=result.total+" matching sources";
  result.items.forEach(d=>$("output").append(docButton(d)));
  const foot=node("div",undefined,"actions");
  if(offset>0)foot.append(action("Previous",()=>listing(offset-30).catch(fail)));
  if(offset+result.items.length<result.total)foot.append(action("Next",()=>listing(offset+30).catch(fail)));
  if(result.total)foot.append(node("small",(offset+1)+"–"+(offset+result.items.length)+" of "+result.total));
  $("output").append(foot);
}
function pageLink(id,p) { return link("Open original PDF", "/pdf/"+encodeURIComponent(id)+"#page="+(p||1)); }
function showDetail(card) { $("detail").replaceChildren(card); $("detail").scrollIntoView({block:"start",behavior:"instant"}); }
function picture(d,p,top,bottom) {
  const fig=node("div",undefined,"figure"), img=node("img");img.alt="Original document, page "+p;
  img.src="/api/page.png?"+new URLSearchParams({doc:d,page:p});fig.append(img);
  if(top!=null && bottom!=null) {
    const stripe=node("div",undefined,"highlight");stripe.style.top=(top*100)+"%";stripe.style.height=((bottom-top)*100)+"%";fig.append(stripe);
  }
  return fig;
}
async function inspect(id, page=null) {
  const token=++detailVersion,d=await api("document",{id});if(token!==detailVersion)return;
  const card=node("div",undefined,"viewer");card.append(node("div","Source document","type"),node("h2",d.name));
  card.append(node("p",d.sections+" indexed passages"+(d.pdf?" · "+d.pages+" PDF pages":""),"dim"));
  if(d.filename && d.filename!==d.name)card.append(node("p","Original filename: "+d.filename,"dim"));
  const controls=node("div",undefined,"actions");
  controls.append(action("Search within document",()=>{
    resetView("search");version++;scope={id:d.id,name:d.name};$("output").replaceChildren();$("status").textContent="";$("shortcuts").replaceChildren();
    $("scope").replaceChildren(action("Searching in "+d.name+"  ×",()=>resetView("search"),"chip"));$("query").focus();
  }));
  if(d.pdf)controls.append(pageLink(id,page));
  else if(d.kind==="file")controls.append(link("Download original file","/original/"+encodeURIComponent(id)));
  if(d.url)controls.append(link("Visit source website",d.url));
  controls.append(action("Close source",clearDetail));card.append(controls);
  if(d.pdf)card.append(picture(id,page||1));
  else if(d.preview.length)d.preview.forEach(x=>card.append(node("p",x.body)));
  else card.append(node("p","Only the document title is available for search."));
  showDetail(card);
}
async function inspectRating(id) {
  const token=++detailVersion,r=await api("rating",{id});if(token!==detailVersion)return;
  const card=node("div",undefined,"viewer");
  card.append(node("div","Selected table row • "+r.metric,"type"),node("h2",r.variant),node("div",r.val+" "+r.units,"value"),node("p",r.name+" · PDF page "+r.page));
  if(r.validity)card.append(node("p","The report prints “Valid Through: "+r.validity+"”. Check the current edition for project use.","dim"));
  const a=node("div",undefined,"actions");a.append(pageLink(r.doc,r.page),action("Close row",clearDetail));card.append(a,picture(r.doc,r.page,r.y0,r.y1));showDetail(card);
}
function highlight(body,q) {
  const p=node("p"),tokens=(q.toLowerCase().match(/[a-z0-9]+/g)||[]).filter(w=>w.length>2);
  for(const part of body.split(/([a-z0-9]+)/ig)) p.append(tokens.includes(part.toLowerCase())?node("mark",part):document.createTextNode(part));
  return p;
}
async function run() {
  if(mode==="library") return listing(0);
  const q=$("query").value.trim(); if(!q)return;
  const id=++version;clearDetail();$("output").replaceChildren();$("status").textContent="Searching the library…";
  const result=await api("search",scope?{q,doc:scope.id}:{q});if(id!==version)return;
  $("status").textContent=result.hits.length ? "Showing the closest matching passages" : "No matching passage found.";
  const rating=result.rating;
  if(rating) {
    const box=node("div",undefined,"answer");
    box.append(node("div","Performance table • "+rating.metric,"type"),node("h2",rating.options.length ? "Choose the printed glazing option" : "No verified row for this selection"));
    if(rating.options.length)box.append(node("p","Open a row to see its value, units and original PDF page."));
    rating.options.forEach(r=>{const b=action(r.variant,()=>inspectRating(r.id).catch(fail),"option");b.append(node("small",r.name+" · p. "+r.page));box.append(b);});
    $("output").append(box);
  }
  if(result.hits.length)$("output").append(node("h2","Source passages"));
  for(const h of result.hits) {
    const c=node("article",undefined,"card");c.append(node("div",h.page?(h.label.startsWith("OCR")?"OCR · PDF PAGE ":"PDF · PAGE ")+h.page:h.label,"type"),node("h3",h.name),highlight(h.body,q));
    const a=node("div",undefined,"actions");a.append(action(h.page?"Inspect page":"Inspect source",()=>inspect(h.doc,h.page).catch(fail)));
    if(h.pdf)a.append(pageLink(h.doc,h.page));if(h.url)a.append(link("Visit publisher",h.url));c.append(a);$("output").append(c);
  }
  if(result.related?.length){$("output").append(node("h2","Related documents"));result.related.forEach(d=>$("output").append(action(d.name,()=>inspect(d.id).catch(fail),"doc")));}
}
$("form").onsubmit=e=>{e.preventDefault();run().catch(fail);};
$("nav-library").onclick=()=>resetView("library");$("nav-search").onclick=()=>resetView("search");
api("stats").then(s=>{stats=s;$("edition").textContent=s.demo?"Demonstration library":"Architectural systems";resetView("search");}).catch(fail);
