from __future__ import annotations

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>SparroW Dashboard</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f1117;color:#e1e4e8;padding:2rem}
h1{font-size:1.8rem;margin-bottom:.5rem;color:#58a6ff}
.sub{color:#8b949e;margin-bottom:2rem;font-size:.95rem}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:1.5rem;margin-bottom:2rem}
.card{background:#161b22;border:1px solid #30363d;border-radius:8px;padding:1.5rem}
.card h2{font-size:1.1rem;color:#c9d1d9;margin-bottom:1rem;border-bottom:1px solid #30363d;padding-bottom:.5rem}
.st{display:flex;justify-content:space-between;padding:.4rem 0}
.st .l{color:#8b949e}.st .v{color:#e1e4e8;font-weight:600}
.pr{display:flex;align-items:center;gap:.5rem;padding:.5rem 0;border-bottom:1px solid #21262d}
.pr:last-child{border-bottom:none}
.dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.dot.g{background:#3fb950}.dot.r{background:#f85149}
.mt{background:#21262d;padding:.2rem .6rem;border-radius:4px;font-size:.85rem;margin:.15rem;display:inline-block}
.mg{display:flex;flex-wrap:wrap;gap:.3rem}
footer{color:#484f58;font-size:.8rem;margin-top:2rem;text-align:center}
a{color:#58a6ff;text-decoration:none}a:hover{text-decoration:underline}
</style>
</head>
<body>
<h1>SparroW</h1>
<p class="sub">OpenAI-compatible proxy/router for keyless free LLM providers</p>
<div class="grid">
<div class="card"><h2>Status</h2><div id="si">Loading...</div></div>
<div class="card"><h2>Statistics</h2><div id="so">Loading...</div></div>
</div>
<div class="card" style="margin-bottom:1.5rem"><h2>Dashboard Access</h2><input id="ak" type="password" placeholder="SparroW API key"><button id="load" type="button">Load data</button></div>
<div class="card" style="margin-bottom:1.5rem"><h2>Providers</h2><div id="pl">Loading...</div></div>
<div class="card"><h2>Available Models</h2><div id="ml" class="mg">Loading...</div></div>
<footer>SparroW &mdash; <a href="/healthz">Health</a> | <a href="/stats">Stats</a> | <a href="/v1/models">Models</a></footer>
<script>
function apiKey(){
  var input=document.getElementById("ak");
  var key=input.value.trim()||sessionStorage.getItem("sparrow_api_key")||window.prompt("Enter SparroW API key");
  if(key){input.value=key;sessionStorage.setItem("sparrow_api_key",key);}
  return key;
}
async function fetchData(path,headers){
  var response=await fetch(path,{headers:headers});
  if(!response.ok)throw new Error("Request failed: "+response.status);
  return response.json();
}
async function loadData(){
  try{
    var key=apiKey();
    if(!key)throw new Error("API key required");
    var headers={"Authorization":"Bearer "+key};
    var h=await fetchData("/healthz",{});
    var s=await fetchData("/stats",headers);
    var p=await fetchData("/v1/providers",headers);
    var m=await fetchData("/v1/models",headers);
    function S(l,v){return '<div class="st"><span class="l">'+l+'</span><span class="v">'+v+'</span></div>';}
    function U(t){if(!t)return'-';var h=Math.floor(t/3600),m=Math.floor(t%3600/60);return h?h+'h '+m+'m':m+'m';}
    function P(r){return(r*100).toFixed(1)+'%';}
    document.getElementById("si").innerHTML=[
      S("Status",h.status=="ok"?'<span style="color:#3fb950">Online</span>':'<span style="color:#f85149">Offline</span>'),
      S("Uptime",U(h.uptime_seconds)),
      S("Providers",String(h.providers||0)),
      S("Routes",String(h.total_routes||0))
    ].join("");
    document.getElementById("so").innerHTML=[
      S("Requests",String(s.total_requests||0)),
      S("Uptime",U(s.uptime_seconds)),
      ...Object.entries(s.providers||{}).map(function(e){
        return S(e[0],e[1].requests+" reqs | "+P(e[1].success_rate)+" ok | "+Math.round(e[1].avg_latency_ms)+"ms");
      })
    ].join("");
    var pd=p.data||[];
    document.getElementById("pl").innerHTML=pd.length?pd.map(function(pp){
      var cls=pp.available?"g":"r";
      var models=(pp.models||[]).map(function(md){return '<span class="mt">'+md+'</span>';}).join("");
      return '<div class="pr"><span class="dot '+cls+'"></span><strong>'+(pp.name||pp.id)+'</strong><span style="color:#8b949e;font-size:.85rem"> ('+pp.id+')</span></div>'
        +'<div style="padding-left:1.5rem;padding-bottom:.8rem"><div class="mg">'+models+'</div></div>';
    }).join(""):'<div style="color:#8b949e">No providers</div>';
    var md=m.data||[];
    document.getElementById("ml").innerHTML=md.length?md.map(function(d){
      return '<span class="mt">'+d.id+'</span>';
    }).join(""):'<div style="color:#8b949e">No models</div>';
  }catch(e){
    document.getElementById("si").innerHTML='<div style="color:#f85149">Error: '+e.message+'</div>';
  }
}
document.getElementById("load").addEventListener("click",loadData);
loadData();
setInterval(loadData,15000);
</script>
</body>
</html>
"""
