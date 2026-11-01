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
  "\nthis.t={BK2,bk2Ready,bk2ServiceView,bk2RenderForm,bk2StatusView,saveBk2,createBk2,installBk2Service,startBk2Restore,bk2HandleRestoreExit,waitBk2,bk2PackageSummary,bk2PackageFiles,renderBk2List,inspectBk2};", ctx);
(async()=>{
  const t=ctx.t;
  t.BK2.jobs={paperpiggy:{},zotero:{}};
  t.BK2.config={service:{installed:false,schema:1},discovered:{zotero:{},browser:{}}};
  const absent={installed:false,pending:false,schema:1,migration_required:false,auto_update_supported:false,can_run:false};
  const legacy={installed:true,needs_update:true,migration_required:true,auto_update_supported:false,can_run:false};
  const ready={installed:true,needs_update:false,pending:false,error:"",schema:2,migration_required:false,auto_update_supported:true,can_run:true,auto_update:{state:"success"}};
  configReplies=[{service:absent}];
  await t.installBk2Service();
  assert.match(notices.pop(),/原生窗口/);
  assert.equal(calls,1);
  ctx.window.pywebview={api:{install_backup_service:async()=>({ok:false,error:"缺少原生备份组件安装器"})}};
  configReplies=[{service:absent}];
  await t.installBk2Service();
  assert.match(el("#bk2-msg").textContent,/缺少原生备份组件/);
  assert.equal(calls,2);
  t.BK2.config={service:legacy};t.bk2ServiceView();
  assert.match(el("#bk2-install").textContent,/一次性迁移.*管理员/);
  let adminCalls=0;
  ctx.window.pywebview.api.install_backup_service=async()=>{adminCalls++;return {ok:true,pending:true};};
  configReplies=[{service:legacy},{service:{...legacy,pending:true}},{service:ready}];
  await t.installBk2Service();
  assert.equal(adminCalls,1);
  assert(t.bk2Ready());
  assert.match(el("#bk2-msg").textContent,/已就绪/);
  ctx.window.pywebview.api.install_backup_service=async()=>{ throw new Error("Unexpected repeated UAC"); };
  configReplies=[{ok:true,service:ready}];
  await t.installBk2Service();
  assert.match(el("#bk2-msg").textContent,/无需再次管理员授权/);

  const failed={...ready,needs_update:true,auto_update:{state:"failed",error:"signature rejected"}};
  t.BK2.config={service:failed};t.bk2ServiceView();
  assert(t.bk2Ready());
  assert.match(el("#bk2-service-title").textContent,/后台可用.*更新未完成/);
  assert.match(el("#bk2-install").textContent,/重试后台更新/);
  assert.match(el("#bk2-service-detail").textContent,/原.*保留|计划.*保留/);
  const pending={...failed,auto_update:{state:"deferred",pending:true}};
  ctx.window.pywebview={api:{}};
  let updates=0;
  ctx.jpost=async(path,body)=>{assert.equal(path,"/backup2/component/update");assert.equal(Object.keys(body).length,0);updates++;calls++;return{ok:true,pending:true,service:pending};};
  configReplies=[{service:failed},{service:pending},{service:ready}];
  await t.installBk2Service();
  assert.equal(updates,1);assert.equal(adminCalls,1);
  assert.match(el("#bk2-msg").textContent,/已就绪/);
  ctx.jpost=async()=>{calls++;throw new Error("Unexpected write");};

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
  const waitingParts={...ready,can_run:true,multipart_update_required:true,needs_update:true,
    auto_update:{state:"pending",pending:true,component_version:1001019000}};
  t.BK2.config={service:waitingParts};
  assert.equal(t.bk2Ready(),false);
  t.bk2ServiceView();
  assert.match(el("#bk2-service-title").textContent,/分包支持更新/);
  assert.match(el("#bk2-service-detail").textContent,/无需管理员/);
  t.bk2StatusView({state:"idle",operation:"backup",history:[]},t.BK2.kind);
  assert.equal(el("#bk2-create").disabled,true);
  const gateCalls=calls;
  await t.createBk2();
  const waitingNotice=notices.pop();
  assert.match(waitingNotice,/自动更新分包支持/);
  assert.doesNotMatch(waitingNotice,/请先配置后台/);
  el("#bk2-enabled").checked=true;el("#bk2-destination").value="D:/fixture-only";
  el("#bk2-days").value="7";el("#bk2-time").value="07:00";el("#bk2-keep").value="2";
  assert.equal(await t.saveBk2(),false);
  assert.match(el("#bk2-msg").textContent,/无需管理员/);
  assert.equal(calls,gateCalls);
  t.BK2.config={service:{...waitingParts,auto_update:{state:"failed",error:"signature rejected",component_version:1001019000}}};
  t.bk2ServiceView();
  assert.equal(el("#bk2-install").textContent,"重试后台更新");
  assert.match(el("#bk2-service-detail").textContent,/重试后台更新.*无需管理员/);
  t.BK2.config={service:{...ready,multipart_update_required:false,auto_update:{state:"success",component_version:1001020000}}};
  assert.equal(t.bk2Ready(),true);
  const split={archive_format:3,part_count:2,size:6000000000,total_size:6000000000,
    path:"D:/fixture/set-001.zip",index_path:"D:/fixture/set.json",index_size:512,
    parts:[{path:"D:/fixture/set-001.zip",name:"set-001.zip",size:4000000000,available:true},
      {path:"D:/fixture/set-002.zip",name:"set-002.zip",size:2000000000,available:false,problem:"文件缺失"}],
    index:{path:"D:/fixture/set.json",size:512,available:true},available:false,missing_files:["set-002.zip"]};
  assert.match(t.bk2PackageSummary(split),/6\.00 GB.*2 个 ZIP.*每包不超过5GB/);
  assert.match(t.bk2PackageFiles(split,false),/总清单[\s\S]*set\.json/);
  assert.match(t.bk2PackageFiles({...split,parts:[{path:"<unsafe>.zip",available:false}]},false),/&lt;unsafe>/);
  t.BK2.kind="zotero";
  configReplies=[{ok:true,items:[split]}];
  await t.renderBk2List("zotero");
  const splitHtml=el("#bk2-list").innerHTML;
  assert.equal((splitHtml.match(/class="bk-item"/g)||[]).length,1);
  assert.match(splitHtml,/整套不完整.*不能恢复/);
  assert.match(splitHtml,/缺少：set-002\.zip/);
  assert.match(splitHtml,/disabled.*data-path/);
  configReplies=[{ok:true,items:[{path:"old.zip",archive_format:2,size:10,available:true}]}];
  await t.renderBk2List("zotero");
  assert.match(el("#bk2-list").innerHTML,/旧格式单 ZIP/);
  assert.doesNotMatch(el("#bk2-list").innerHTML,/查看整套文件/);
  ctx.jpost=async(path,body)=>{calls++;assert.equal(path,"/backup2/inspect");assert.equal(body.path,"D:/fixture/set-002.zip");
    return {...split,ok:true,available:true,manifest:{format:2,kind:"zotero",roots:[]},
      parts:split.parts.map(part=>({...part,available:true})),warnings:[]};};
  await t.inspectBk2("D:/fixture/set-002.zip");
  assert.equal(t.BK2.restore.path,"D:/fixture/set-002.zip");
  assert.match(el("#bk2-restore-info").textContent,/2 个 ZIP[\s\S]*自动合并/);
  assert.match(el("#bk2-restore-files").innerHTML,/总清单/);
  assert.equal(el("#bk2-restore").hidden,false);
  ctx.jpost=async()=>{calls++;return {ok:false,error:"缺少分包：set-002.zip"};};
  await t.inspectBk2("D:/fixture/set-001.zip");
  assert.equal(t.BK2.restore,null);
  assert.equal(el("#bk2-restore").hidden,true);
  assert.match(notices.pop(),/set-002\.zip/);
  const noRestoreCalls=calls;
  await t.startBk2Restore();
  assert.equal(calls,noRestoreCalls);
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
