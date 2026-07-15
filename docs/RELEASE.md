# PaperPiggy 发布与自动更新方案

> 2026-07-13 拟定，2026-07-14 更新（目录重组 + 许可证合规）。
> **2026-07-14 二次更新（发布形态定案，推翻了本文档若干旧结论，以本段为准）**：
>   ① **只发 Inno 安装器，砍掉便携 zip**（§2b 已废止，原因见 installer/paperpiggy.iss §为什么砍掉便携 zip）。
>   ② **用户级安装**（`PrivilegesRequired=lowest`，`{autopf}` → `%LOCALAPPDATA%\Programs`），不弹 UAC；
>      安装向导保留「选择位置」页，用户可装到 `D:\PaperPiggy`。
>   ③ **数据与程序同目录**：安装器带 `portable.txt`，索引/模型/wiki/0_Agent* 全在安装目录内。
>      安装目录不可写时 config.py 自动回退 `%LOCALAPPDATA%\PaperPiggy`。
>   ④ 数据目录由 `LocalKB` 改名为 **`PaperPiggy`**（环境变量 `LOCALKB_*` 不变）。
>
> 基于当前架构（内嵌 python-build-standalone CPython + app/ 纯源码 + pywebview/WebView2
> + 首启下载 onnx 模型 + MinGit）。

---

## 0. 构建环境（先看这里）

### 0.1 构建素材在哪

| 素材 | 位置 | 在 git 里？ | 丢了怎么办 |
|---|---|---|---|
| **Python 运行时 + 全部依赖** | `build/py312/` (~800M) | ❌ | 按 §0.2 重建（**这是唯一不可自动重建的东西**） |
| **MinGit** | `build/assets/MinGit/` (~90M) | ❌ | `python src/fetch_mingit.py` |
| **ONNX 模型** | `D:\00Zotero知识库\rag\data\models\` | ❌ | **母本，勿删**。重新量化要几小时 |
| 源码 | `src/` | ✅ | `git checkout` |

### 0.1b Inno Setup（ISCC）装在**用户级路径** —— 别因 `where ISCC` 查不到就以为出不了安装器

编译安装器 `.exe` 用的 `ISCC.exe` 装在 **`%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`**
（本机 = `C:\Users\Lsj13\AppData\Local\Programs\Inno Setup 6\`），**不在 `Program Files`**——
本项目一贯走用户级安装（不弹 UAC），Inno Setup 也是这么装的。

⚠️ **踩过（2026-07-15）**：只跑 `where ISCC` 或只翻 `C:\Program Files*` 会查不到它，从而**误判成
「Inno 没装、出不了安装器」**。别下这个结论 —— `installer/build_installer.py` 的 `ISCC_CANDIDATES`
**第一个候选就是这个用户级路径**（见该文件 `ISCC_CANDIDATES`），`build_installer.py` 能自动找到、
正常产出 `PaperPiggy-<ver>-win64.exe`。要确认它到底找没找到，直接跑 build_installer 看日志那行
`编译安装器：<ISCC 路径>` 即可，不要凭 `where` 的空结果臆断。

真没装时才需要装：`winget install JRSoftware.InnoSetup`（默认也落用户级）。**没装也不致命**：
build_installer 会打印「没找到 ISCC.exe，跳过安装器」、不报错、仍产出 `paper-piggy-app-<ver>.zip`
更新包（存量用户能一键升级，只是新用户装机缺 `.exe`）。

### 0.2 重建 `build/py312`（唯一不可自动重建的环节）

`build_bundle.py` 只**检查** `python/python.exe` 存不存在，**不会创建它**。所以这一步必须手工做：

```powershell
# ① 下 python-build-standalone（CPython 3.12，install_only 版）
#    https://github.com/astral-sh/python-build-standalone/releases
#    选 cpython-3.12.x+*-x86_64-pc-windows-msvc-install_only.tar.gz
#    解压后把里面的 python/ 目录放到 build/py312/

# ② 装依赖 —— 一定要用 lock，不要用 requirements.txt
build\py312\python.exe -m pip install -r src\requirements.lock

# ③ 补 VC++ 运行库（❗ 少了这步，干净机上本地模式必崩 WinError 1114）
copy C:\Windows\System32\msvcp140.dll    build\py312\
copy C:\Windows\System32\msvcp140_1.dll  build\py312\
#    要求 ≥14.40。onnxruntime 的导入表同时需要 msvcp140 和 msvcp140_1，
#    而 python-build-standalone 只自带 vcruntime140/_1。
#    site-packages 里那几份救不了：numpy/pyarrow 的副本被 delvewheel 改名成 msvcp140-<hash>.dll。

# ④ 验证
build\py312\python.exe -c "import onnxruntime, lancedb, pypdfium2, docx; print('OK')"
```

### 0.3 许可证红线

⛔ **不得引入 AGPL / Polyform Noncommercial / SSPL 依赖。**
本项目曾因 `pymupdf4llm` → `pymupdf_layout`(Polyform NC) → `PyMuPDF`(AGPL) 而**无法合法开源发布**，
2026-07 已换成 `pypdfium2`(BSD/Apache) 解决。新增依赖前先扫许可证，见 [THIRD-PARTY-NOTICES.md](../THIRD-PARTY-NOTICES.md)。

---

## 0.9 发布检查单（每次发版逐条打勾）

**代码与指引**
- [ ] `python gen_mcp_doc.py --check` 退出码 0（工具表未漂移）
- [ ] `python check_guides.py` 全绿（指引与代码一致）
- [ ] `CHANGELOG.md` 已更新
- [ ] 版本号只改了 `config.APP_VERSION` 一处

**构建产物**
- [ ] `bundle/python/` 下有 `msvcp140.dll` + `msvcp140_1.dll`（≥14.40）
- [ ] `python-docx` 已装（否则 Word 导出静默降级为 .md）
- [ ] `models_manifest.json` 存在，且直链可匿名下载（私有仓库的 Release 资产会 404）
- [ ] `app/version.json` 的 sha256 与磁盘文件一致
- [ ] 包内**没有** `app/data/`、`app/logs/` 残留
- [ ] 包内**没有** `settings.json` 里的 API 密钥
- [ ] bundle **根目录**没有非空的 `data/`、`0_Agent交付物/`、`0_Agent资料库/`
      （数据与程序同目录之后，自测这个包就会在包根留下真实数据 —— `build_installer.check_bundle()`
      会中止出包并报警，别手贱跳过）
- [ ] 安装器**带** `portable.txt`（由 .iss 从 `installer\portable.txt` 装入 = 数据同目录开关）
- [ ] 安装器是**用户级**的（`PrivilegesRequired=lowest`）—— 与上一条是一套，只改一个必崩

**干净机验收**（没装 VC++ 2015-2022、没装 WebView2 的全新 Windows）
- [ ] `bundle\python\python.exe -c "import onnxruntime"` 不报 WinError 1114
- [ ] 双击快捷方式弹出**原生窗口**（弹系统浏览器 = WebView2 没装上）
- [ ] 首启向导能下模型（本地模式）/ 能填 key（API 模式）
- [ ] 能建库、能检索、能深索出正文
- [ ] 关窗后进程干净退出（任务管理器里没有残留 python.exe）
- [ ] 装到默认位置：数据落**安装目录**的 `data\`（不是 C 盘用户目录）
- [ ] 在向导里改装到 `D:\PaperPiggy`：数据跟着落 `D:\PaperPiggy\data\`
- [ ] 硬把它装进 `C:\Program Files\PaperPiggy`（不可写）：**不崩**，数据回退
      `%LOCALAPPDATA%\PaperPiggy\data`，日志里有 `[config] 安装目录不可写` 提示
- [ ] 覆盖安装新版：`data\`、`0_Agent交付物\`、`0_Agent资料库\` 原样还在
- [ ] 卸载后：上述三个目录**仍在**安装目录里（用户数据不能被卸载带走）

**隐私**
- [ ] `git ls-files | grep -iE "settings\.json|papers\.jsonl|\.key"` 为空

---

## 一句话结论

**不要做单文件 exe（PyInstaller onefile），保持现有目录形态；Windows ~~发 Inno Setup 安装器 + 便携 zip 双形态~~ → 只发 Inno Setup 安装器（用户级 + 数据同目录，便携 zip 已砍，见文首更新）；自更新自写（只换 app/，约 200 行）—— ✅ **已接线**（1.0.x 起）：`server /update/{check,download,status,mirror}` + `launcher.apply_update()` 拉起独立 `updater.py --apply` 换 `app\` 并重启，顶栏 `#up-badge` 提示新版；数据安全保证见 `updater.apply()`（只碰 `app\`）；macOS 目前不打包、只从源码运行（见 [MAC-从源码运行.md](../MAC-从源码运行.md)），暂不买 $99 Apple 开发者账号；大陆分发用 GitHub Release 主源 + 多镜像前缀 + Cloudflare R2 免费第二源。**

---

## 1. 为什么不做 PyInstaller 单文件 exe

- onnxruntime 在 PyInstaller 下的 `DLL load failed` 问题 2025 年仍有新 issue，需要各种 workaround 且不稳定；lancedb/pyarrow 同样有打包断链问题。当前"内嵌 CPython + 源码目录"形态**恰好绕开了全部这些坑**。
- onefile 运行时自解压行为酷似恶意 packer，是杀软误报重灾区（PyInstaller 官方 FAQ 级问题）；`--onedir` 好很多，但那就跟现在的目录形态没区别了，白折腾。
- MSIX 非商店分发必须有受信任证书签名，无证书直接出局。

**结论：现有 bundle 形态就是正确答案，发布层只需要在外面"套壳"。**

## 2. Windows 发布形态（单轨：只发安装器）

### 2a. Inno Setup 安装器（唯一发布形态）
- 对现有目录零侵入：把 bundle 整个打进去，默认装到 `%LOCALAPPDATA%\Programs\PaperPiggy`
  （`PrivilegesRequired=lowest` 之下 `{autopf}` 就解析到这里；**无需管理员权限、不弹 UAC**）。
  安装向导的「选择位置」页**刻意保留** —— 包 800M + 模型 1~2G + 索引若干 G，用户想装 `D:\` 就该让他装。
- **数据与程序同目录**：安装器带 `portable.txt`，索引/模型/wiki/`0_Agent*` 全落在安装目录内。
  一个文件夹搬走就能换电脑，C 盘不占。卸载重装不丢索引 —— Inno 只删自己装进去的文件。
  兜底：万一用户把它装进 `Program Files`（不可写），`config._writable()` 探测到就回退
  `%LOCALAPPDATA%\PaperPiggy`，不崩。
- 注意事项：安装器**不要命名为 setup.exe**（加重 Defender 盯梢），填全 Publisher/ProductName/版本元数据；加一段检测注册表无 WebView2 时运行 Evergreen Bootstrapper（约 2MB，随包携带）的脚本 —— Win11 出厂预装，Win10 绝大多数已推送但仍有漏网。
- Inno Setup 支持中文向导，脚本 100 行以内。

### 2b. ~~便携 zip~~ ⛔ 已于 1.0.0 砍掉
数据既然与程序同目录，「删掉旧文件夹、解压新版」—— 便携软件最常规的升级姿势 ——
就会把用户的索引、wiki、API key、写好的论文一次性删光。走安装器升级则不会
（Inno 只覆盖 `app\` 和 `python\`，不碰 `data\`）。所以只发安装器。
`config.py` 里的 `portable.txt` 分支保留（它现在的语义是「数据同目录」，安装器正是靠它），
但打包链路不再产出任何 zip。详见 `installer/paperpiggy.iss` 文件头。

### 2c. 代码签名（决定 SmartScreen 体验）
| 选项 | 2026 现状 | 结论 |
|---|---|---|
| 不签名 | SmartScreen 按单文件 hash 攒信誉，**每发新版清零**；用户要点"更多信息→仍要运行" | 起步可接受，README 写清步骤 |
| Azure Trusted Signing（已更名 Artifact Signing）| $9.99/月，但个人通道仅美/加且暂停新申请 | **中国个人开发者用不了** |
| EV 证书 | $280+/年；2024-08 起 EV **不再有即时信誉**，和 OV 一视同仁 | 性价比已崩，不值 |
| **Certum 开源代码签名证书** | 首年 €69 含读卡器，续期 €29，支持个人身份验证，中国可办 | **要签就选它**：信誉跨版本累积 + 显著降 AV 误报 |

建议：首发裸奔 + 每次发版主动提交 Microsoft 误报申诉页洗白；用户量起来后花 €69 上 Certum。

## 3. 应用自更新（自写，不引框架）

现有架构已为此铺好路：app/（代码）与 data/models/（用户资产）彻底分离，更新=只换 app/。

### 设计（约 200 行）
1. **版本地基**（✅ 已落地）：唯一版本字面量是 `config.APP_VERSION`（当前值以代码为准，别在文档里抄）；`mcp_server.py` 的 serverInfo 已引用 `C.APP_VERSION`（不再硬编码）；`build_bundle.py` 打包时生成 `app/version.json`（版本+构建日期+app.zip 的 sha256）。`check_guides ⑤` 断言版本只此一处。
2. **检查**：server 后台（可顺搭现有 `_auto_update_loop`，注意与"知识库自动更新"是两回事，UI 文案要区分）或设置页按钮，请求 GitHub API latest release → 比版本 → UI 顶栏提示"有新版 vX.Y.Z"。
3. **下载**：只下 `app.zip`（纯源码，约几 MB，不含 python/models）→ sha256 校验 → 解压到 `app_new/`。多镜像 fallback 复用 models_manifest.json 的思路（见 §5）。
4. **替换**：Windows 不能替换运行中的 exe/被占用文件，所以交给**独立小脚本**：主进程写 `update_pending` 标记后退出 → run_localkb.py 启动时发现标记 → `app→app_old, app_new→app` → 失败回滚 `app_old` → 正常启动。（.py 文件本身不锁，但 server 进程活着时换代码不生效，重启替换最干净。）
5. **大件**：python/、models/ 极少变；若哪天 requirements 变了，release notes 里标记"需要重装完整包"，更新器检测到 `min_full_version` 字段就引导下载完整安装器。

### 备选：Velopack
当下唯一"活着且官方支持 Python"的完整更新框架（PyPI `velopack` 1.2.0，2026-06）：delta 增量 + 安装器生成 + 更新一条龙，要求目录形态（正好契合）+ 一个 exe 主入口 + 打包机装 .NET SDK。
**取舍**：功能强但引入外部框架和 exe 入口改造；自写方案完全够用且可控。建议先自写，未来包大了再考虑 delta。
（PyUpdater 已死；tufup 是其官方继任但小众且要自管 TUF 密钥；WinSparkle 对 Python 应用收益不大。）

## 4. Mac 版：能做，但分发体验有硬门槛

**技术上无 Mac 也可行**（全部在 GitHub Actions macos runner 上做，公有仓库免费不限量）：
- python-build-standalone 有 aarch64-apple-darwin 版；onnxruntime/lancedb/pyobjc 都有 arm64 wheel。
- pywebview 在 mac 走系统 WKWebView（pyobjc），无需额外运行时（等价 WebView2 但保证预装）。
- 需要补的活：包成 .app（Info.plist）、启动器替换 .bat/.vbs、MinGit 换成检测系统 git（无则 wiki_vcs 自动退回 .history 快照 —— 已有降级路径）、CI 里 `codesign --deep -s -` 做 ad-hoc 签名（Apple Silicon 硬要求，免费）。

**分发门槛（Gatekeeper 2026 现状）**：
- Sequoia (15) 起「右键→打开」绕过**已被移除**；用户必须去 系统设置→隐私与安全性→"仍要打开"。
- Tahoe (26) 还要输管理员密码，未签名+quarantine 的 app 常直接报"已损坏"。
- `xattr -dr com.apple.quarantine` 仍有效，但要用户开 Terminal。
- 体面体验必须 Apple Developer $99/年 + 公证（CI 可全自动）。

**建议**：Windows 版先发；mac 出"实验性"包（CI 产出 + 文档写清放行步骤），找一两个有 Mac 的朋友实测能否启动；等有真实 mac 用户需求再花 $99。

## 5. 大陆分发（下载源架构）

| 层级 | 源 | 成本 | 说明 |
|---|---|---|---|
| 主源 | GitHub Release | $0 | 无限量、海外快；大陆直连常见 50KB/s 量级，不能独用 |
| 加速 | ghproxy 类前缀镜像（如 ghfast.top 等） | $0 | 5-7MB/s，但公益服务会死，**镜像列表本身做成可远程更新的 json** |
| 第二官方源 | **Cloudflare R2 + 自定义域** | $0（10GB 存储+出站流量永久免费） | 大陆不快但稳定合法；够放安装包+全部模型 |
| 按量加速 | 腾讯云 COS | 0.5 元/GB 下行 | 量小=每月几块钱；COS 默认域名无需备案；看下载量再上 |

- jsDelivr 单文件 50MB 上限 + 主域污染史 → 出局；Gitee 单附件 100MB + 新仓库人工审核 → 只配当小文件兜底。
- models_manifest.json 的多镜像机制直接复用到 app.zip / 安装器下载上。

## 6. 落地顺序建议

1. **本轮**（发布前）：加 `APP_VERSION` + version.json 地基（半小时的活）。
2. **v1.0 发布**：build_bundle 产出 → Inno Setup 打包（不再出便携 zip）→ GitHub Release（含 app.zip + sha256）→ R2 同步一份 → README/小红书写清 SmartScreen"仍要运行"步骤。
3. **v1.0.x**：实装自更新（检查+下载+重启替换+回滚），用第一次小版本迭代实测更新链路。
4. **之后按需**：Certum 证书（€69）→ mac 实验包（GitHub Actions）→ COS 加速 → （远期）$99 mac 公证。

## 主要来源

- PyInstaller×onnxruntime：onnxruntime#25193、pyinstaller#8083；lancedb#2146/#2202
- SmartScreen 信誉与 EV 变化：MS Learn smartscreen-reputation、advancedinstaller.com
- Artifact Signing（原 Trusted Signing）个人通道限美/加且暂停：MS Learn artifact-signing FAQ
- Certum 开源证书 €69：shop.certum.eu；实操记录 piers.rocks (2025-10)
- Velopack Python 支持：docs.velopack.io/getting-started/python；PyUpdater→tufup：GitHub dennisvang/tufup
- Gatekeeper Sequoia/Tahoe 收紧：mjtsai.com、intego.com、wiki.hacks.guide
- GitHub Actions macOS 公有仓库免费：docs.github.com actions runner pricing
- R2 出站免费：developers.cloudflare.com/r2/pricing；COS 0.5元/GB：cloud.tencent.com
- WebView2 分发：MS Learn webview2 distribution
