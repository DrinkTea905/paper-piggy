"""Run the header's real JavaScript against asynchronous status and click scenarios."""
import os
from pathlib import Path
import shutil
import subprocess
import unittest


@unittest.skipUnless(shutil.which("node"), "Node.js is required for header behavior checks")
class BackupHeaderFrontendTests(unittest.TestCase):
    def test_status_notifications_navigation_and_read_only_polling(self):
        script = r'''
const vm = require("vm"), fs = require("fs"), assert = require("assert");
const source = fs.readFileSync(process.argv[2], "utf8");
const start = source.indexOf("  const BK_HEADER ="), end = source.indexOf("  function wireBackup2()", start);
assert(start > 0 && end > start);
const elements = new Map();
const el = key => {
  if (!elements.has(key)) elements.set(key, {
    hidden:false, textContent:"", innerHTML:"unchanged", className:"", dataset:{}, open:false,
    attributes:{}, events:{}, setAttribute(k,v){this.attributes[k]=v},
    addEventListener(k,v){this.events[k]=v}
  });
  return elements.get(key);
};
const timers = new Map(), intervals = [], events = {}, reads = [], toasts = [], opens = [], confirms = [];
let timerId=0, loads=0, accept=false, response={ok:true,jobs:[]}, failure=null, pending=null;
const BK2={kind:"paperpiggy",dirty:false};
const ctx={
  BK2, $:el, AbortController,
  document:{hidden:false,addEventListener(k,v){events[k]=v}},
  bk2Name:k=>k==="zotero"?"Zotero 完整备份":"小猪备份",
  bk2When:String, bk2Error:e=>e.message || String(e), flashToast:s=>toasts.push(s),
  uiConfirm:async(s,opts)=>{confirms.push({s,opts});return accept},
  openSettings:(...args)=>opens.push(args), loadBackup:async()=>{loads++},
  fetch:async(url,opts)=>{
    reads.push({url,opts});
    if(pending)return pending(opts.signal);
    if(failure)throw failure;
    return {ok:true,json:async()=>response};
  },
  setTimeout(fn){const id=++timerId;timers.set(id,fn);return id},
  clearTimeout(id){timers.delete(id)}, setInterval(fn,ms){intervals.push({fn,ms})}
};
vm.createContext(ctx);
vm.runInContext(source.slice(start,end)+"\nthis.t={BK_HEADER,bk2HeaderView,refreshBackupHeader,openBackupHeader,wireBackupHeader};",ctx);
const t=ctx.t;
const job=(kind,rest={})=>({kind,enabled:true,configured:true,running:false,operation:"backup",stage:"",last_success:"",issue:null,...rest});
const issue=(id,message="fixture source missing")=>({id,level:"failed",message,at:"2026-10-30T12:00:00+08:00",operation:"backup"});
const refresh=async jobs=>{response={ok:true,jobs};failure=null;await t.refreshBackupHeader()};
(async()=>{
  await refresh([]);
  assert.equal(el("#btn-backup").dataset.backupState,"idle");
  assert.equal(el("#backup-header-badge").hidden,true);
  assert.match(el("#btn-backup").title,/尚未配置/);
  assert.doesNotMatch(el("#btn-backup").title,/备份成功/);

  await refresh([job("paperpiggy",{running:true}),job("zotero",{issue:issue("a","<unsafe> source missing")})]);
  assert.equal(el("#btn-backup").dataset.backupState,"failed");
  assert.equal(el("#backup-header-badge").textContent,"失败");
  assert.match(el("#btn-backup").attributes["aria-label"],/备份，失败/);
  assert.match(el("#btn-backup").title,/Zotero[\s\S]*<unsafe>/);
  assert.equal(toasts.length,1);assert.match(toasts[0],/点右上角/);
  await t.refreshBackupHeader();assert.equal(toasts.length,1);
  await refresh([job("paperpiggy"),job("zotero",{running:true,issue:issue("a")})]);
  assert.equal(el("#btn-backup").dataset.backupState,"failed");assert.equal(toasts.length,1);
  assert.match(el("#bk2-header-issue").textContent,/^$/);

  await t.openBackupHeader();
  assert.equal(BK2.kind,"zotero");assert.equal(loads,1);
  assert.equal(opens[0][0],"sec-backup");assert.equal(opens[0][1].skipBackupLoad,true);
  assert.equal(el(".bk2-history").open,true);
  assert.match(el("#bk2-header-issue").textContent,/fixture source missing[\s\S]*正在重试/);
  assert.equal(el("#bk2-header-issue").innerHTML,"unchanged");
  assert.equal(confirms.length,0);

  await refresh([job("paperpiggy"),job("zotero",{last_success:"2026-10-30T13:00:00+08:00"})]);
  assert.equal(el("#btn-backup").dataset.backupState,"idle");
  assert.equal(el("#bk2-header-issue").hidden,true);
  await refresh([job("paperpiggy",{issue:{...issue("w"),level:"warning"}}),job("zotero",{running:true})]);
  assert.equal(el("#backup-header-badge").textContent,"进行中");
  await refresh([job("paperpiggy",{issue:{...issue("w"),level:"warning"}})]);
  assert.equal(el("#backup-header-badge").textContent,"需留意");
  failure=new Error("network down");await t.refreshBackupHeader();
  assert.equal(el("#backup-header-badge").textContent,"状态未知");assert.equal(toasts.length,1);
  assert.match(el("#btn-backup").title,/network down/);

  await refresh([job("paperpiggy",{issue:issue("b")}),job("zotero",{issue:issue("c")})]);
  assert.equal(toasts.length,2);assert.match(toasts[1],/小猪[\s\S]*Zotero/);
  failure=new Error("offline");await t.refreshBackupHeader();
  assert.equal(el("#backup-header-badge").textContent,"失败");
  assert.match(el("#btn-backup").title,/offline/);assert.equal(toasts.length,2);

  // A header click cannot discard another task's edits or save/submit anything.
  BK2.kind="zotero";BK2.dirty=true;const loadBefore=loads;
  await t.openBackupHeader();
  assert.equal(BK2.kind,"zotero");assert.equal(BK2.dirty,true);assert.equal(loads,loadBefore);
  assert.match(confirms.at(-1).s,/未保存/);
  accept=true;await t.openBackupHeader();
  assert.equal(BK2.kind,"paperpiggy");assert.equal(BK2.dirty,false);assert.equal(loads,loadBefore+1);
  BK2.dirty=true;await t.openBackupHeader();
  assert.equal(BK2.dirty,true);assert.equal(loads,loadBefore+1);
  BK2.saving=true;const kindBefore=BK2.kind;
  await t.openBackupHeader();assert.equal(BK2.kind,kindBefore);assert.equal(loads,loadBefore+1);
  assert.match(toasts.at(-1),/正在保存/);BK2.saving=false;

  failure=null;
  let resolvePending;
  pending=()=>new Promise(resolve=>{resolvePending=resolve});
  const inFlight=t.refreshBackupHeader(), readBefore=reads.length;
  await t.refreshBackupHeader();assert.equal(reads.length,readBefore);
  resolvePending({ok:true,json:async()=>({ok:true,jobs:[]})});await inFlight;pending=null;
  ctx.document.hidden=true;await t.refreshBackupHeader();assert.equal(reads.length,readBefore);
  ctx.document.hidden=false;
  pending=signal=>new Promise((_,reject)=>signal.addEventListener("abort",()=>reject(Object.assign(new Error("abort"),{name:"AbortError"}))));
  const stalled=t.refreshBackupHeader();assert.equal(t.BK_HEADER.loading,true);
  [...timers.values()].forEach(fn=>fn());await stalled;pending=null;
  assert.equal(t.BK_HEADER.loading,false);assert.match(t.BK_HEADER.error,/超时/);
  await refresh([]);assert.equal(t.BK_HEADER.error,"");

  t.wireBackupHeader();
  assert.equal(intervals.at(-1).ms,20000);assert.equal(typeof events.visibilitychange,"function");
  assert.equal(typeof el("#btn-backup").events.click,"function");
  await new Promise(resolve=>setImmediate(resolve));
  const beforeVisible=reads.length;events.visibilitychange();
  await new Promise(resolve=>setImmediate(resolve));assert.equal(reads.length,beforeVisible+1);
  assert(reads.every(r=>r.url==="/backup2/summary" && !r.opts.method && r.opts.cache==="no-store"));
  console.log("PASS persistent failure, read-only polling, timeout, visibility, navigation and dirty form");
})().catch(e=>{console.error(e);process.exitCode=1});
'''
        source = Path(__file__).resolve().parents[1] / "web" / "app.js"
        result = subprocess.run(
            [shutil.which("node"), "-", str(source)], input=script,
            capture_output=True, text=True, encoding="utf-8", timeout=20,
            creationflags=0x08000000 if os.name == "nt" else 0,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS persistent failure", result.stdout)

    def test_button_keeps_its_label_and_both_theme_status_styles(self):
        web = Path(__file__).resolve().parents[1] / "web"
        html = (web / "index.html").read_text(encoding="utf-8")
        button = html[html.index('<button id="btn-backup"'):html.index('<button id="btn-settings"')]
        self.assertIn("<span>备份</span>", button)
        self.assertNotIn("top-action-label", button)
        self.assertIn('aria-live="polite"', button)
        css = (web / "style.css").read_text(encoding="utf-8")
        self.assertIn('#btn-backup { flex: 0 0 auto; white-space: nowrap; }', css)
        self.assertIn(':root[data-theme="dark"] #btn-backup[data-backup-state="failed"]', css)
        self.assertIn('.topbar > .actions { flex-wrap: wrap; justify-content: flex-end; }', css)


if __name__ == "__main__":
    unittest.main()
