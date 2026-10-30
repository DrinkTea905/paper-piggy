"""Exercise backup UI contracts without a browser window or native authorization."""
import shutil
import subprocess
import unittest
from pathlib import Path


@unittest.skipUnless(shutil.which("node"), "Node.js is required for frontend contract checks")
class BackupFrontendContracts(unittest.TestCase):
    def test_native_install_first_use_and_sensitive_restore_contracts(self):
        script = r"""
const vm = require("vm"), fs = require("fs"), assert = require("assert");
const file = process.argv[2];
const source = fs.readFileSync(file, "utf8");
const start = source.indexOf("  const _bkSize =");
const end = source.indexOf("  wireBackup2();", start);
assert(start >= 0 && end > start);
const elements = new Map();
const el = selector => {
  if (!elements.has(selector)) elements.set(selector, {
    textContent:"", innerHTML:"", hidden:false, disabled:false, value:"",
    checked:false, dataset:{}, classList:{toggle(){}},
    setAttribute(){}, querySelectorAll(){return []}, scrollIntoView(){}
  });
  return elements.get(selector);
};
let calls = 0, notices = [], confirmations = [], configReplies = [];
const ctx = {
  window:{}, $:el, $$:()=>[], num:String,
  esc:s=>String(s).replace(/</g,"&lt;"),
  jget:async()=>{ calls++; return configReplies.shift(); },
  jpost:async()=>{ calls++; throw new Error("Unexpected write"); },
  uiNotice:async text=>notices.push(text),
  uiConfirm:async text=>{ confirmations.push(text); return false; },
  setTimeout:callback=>callback(), clearInterval(){}, setInterval(){}
};
vm.createContext(ctx);
vm.runInContext(source.slice(start,end) +
  "\nthis.t={BK2,bk2Ready,bk2RenderForm,bk2StatusView,saveBk2,createBk2,installBk2Service,startBk2Restore,bk2HandleRestoreExit,waitBk2};", ctx);
(async()=>{
  const t=ctx.t;
  t.BK2.jobs={paperpiggy:{},zotero:{}};
  t.BK2.config={service:{installed:false,schema:1},discovered:{zotero:{},browser:{}}};
  await t.installBk2Service();
  assert.match(notices.pop(),/原生窗口/);
  assert.equal(calls,0);
  ctx.window.pywebview={api:{install_backup_service:async()=>({ok:false,error:"缺少原生备份组件安装器"})}};
  await t.installBk2Service();
  assert.match(el("#bk2-msg").textContent,/缺少原生备份组件/);
  assert.equal(calls,0);
  ctx.window.pywebview.api.install_backup_service=async()=>({ok:true,pending:true});
  configReplies=[
    {service:{installed:false,pending:true,schema:1}},
    {service:{installed:true,needs_update:true,schema:1}},
    {service:{installed:true,needs_update:false,pending:false,error:"",schema:1}}
  ];
  await t.installBk2Service();
  assert.equal(calls,3);
  assert(t.bk2Ready());
  assert.match(el("#bk2-msg").textContent,/已就绪/);
  ctx.window.pywebview.api.install_backup_service=async()=>{ throw new Error("Unexpected repeated UAC"); };
  configReplies=[{ok:true,service:{installed:true,needs_update:false,pending:false,error:"",schema:1}}];
  await t.installBk2Service();
  assert.equal(calls,4);
  assert.match(el("#bk2-msg").textContent,/无需再次管理员授权/);

  t.BK2.kind="zotero";t.BK2.config.discovered={zotero:{},browser:{}};
  t.BK2.config.notice="换机后请重新选择本机来源和备份位置";
  t.bk2RenderForm();
  assert.equal(el("#bk2-data-dir").value,"");
  assert.equal(el("#bk2-profile-dir").value,"");
  assert.equal(el("#bk2-keep").value,2);
  assert.equal(el("#bk2-machine-notice").hidden,false);
  assert.match(el("#bk2-machine-notice").textContent,/重新选择/);
  el("#bk2-enabled").checked=true;
  const before=calls;
  assert.equal(await t.saveBk2(),false);
  assert.equal(calls,before);
  assert.match(el("#bk2-msg").textContent,/选择备份位置/);

  t.BK2.kind="paperpiggy";
  t.BK2.jobs.paperpiggy={destination:"D:/fixture-only"};
  t.BK2.dirty=false;
  el("#bk2-with-key").checked=true;
  await t.createBk2();
  assert.match(confirmations.pop(),/API.*密钥.*不加密/);
  assert.equal(calls,before);

  t.BK2.restore={kind:"zotero",path:"fixture.zip",hasBrowser:false};
  el("#bk2-restore-mode").value="new";
  el("#bk2-restore-destination").value="";
  await t.startBk2Restore();
  assert.match(el("#bk2-msg").textContent,/新的空目录/);
  assert.equal(calls,before);

  t.bk2StatusView({state:"failed",error:"fixture failure",operation:"restore",
    history:[{at:"2026-10-30",state:"interrupted",reason:"<unsafe>"}]},"paperpiggy");
  assert.match(el("#bk2-history").innerHTML,/意外中断/);
  assert.match(el("#bk2-history").innerHTML,/&lt;unsafe>/);
  assert.match(el("#bk-runtime-detail").textContent,/fixture failure/);
  t.bk2StatusView({state:"cancelled",error:"已在排队阶段取消",operation:"backup",history:[]},"paperpiggy");
  assert.match(el("#bk-runtime-title").textContent,/已停止/);
  configReplies=[
    {state:"cancelled",error:"已在排队阶段取消",request_id:"cancel-request",finished_at:"now",result:{},history:[]},
    {items:[]}
  ];
  const cancelled=await t.waitBk2("paperpiggy",{queued:true,request_id:"cancel-request"},"before");
  assert.equal(cancelled.cancelled,true);
  t.bk2StatusView({state:"success",operation:"backup",running:false,result:{},
    source_coverage:{schema:1,kind:"zotero",existing:100,missing:93,linked:2},history:[]},"paperpiggy");
  assert.match(el("#bk-runtime-detail").textContent,/现存附件 100，原未在来源中 93/);
  assert.match(el("#bk-runtime-detail").textContent,/目录外链接 2/);
  assert.equal(el("#bk-runtime").className,"bk-runtime ok");
  assert.doesNotMatch(el("#bk-runtime-title").textContent,/失败|未完成|需处理/);
  t.bk2StatusView({state:"success",operation:"backup",running:false,result:{},history:[]},"paperpiggy");
  assert.doesNotMatch(el("#bk-runtime-detail").textContent,/按来源原样备份/);
  let closed=0;
  ctx.window.pywebview.api.close_for_backup_restore=async()=>{ closed++; return {ok:true}; };
  assert.equal(await t.bk2HandleRestoreExit({requires_exit:true}),true);
  assert.equal(closed,1);
  assert.equal(t.BK2.waitingExit,true);
  assert.equal(el("#bk2-create").disabled,true);
  assert.equal(el("#bk2-save").disabled,true);
  assert.match(notices.pop(),/MCP[\s\S]*退出小猪[\s\S]*不要重新打开/);
  console.log("PASS native installer, needs_update, empty sources, sensitive confirmation and history");
})().catch(error=>{ console.error(error);process.exitCode=1; });
"""
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [shutil.which("node"), "-", str(root / "web/app.js")],
            input=script, capture_output=True, text=True, encoding="utf-8",
            creationflags=0x08000000 if __import__("os").name == "nt" else 0,
            timeout=20,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertIn("PASS native installer", result.stdout)


if __name__ == "__main__":
    unittest.main()
