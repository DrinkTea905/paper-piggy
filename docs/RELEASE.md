# LocalKB / PaperPiggy 发布与自动更新方案

> 2026-07-13 拟定。基于当前架构（内嵌 python-build-standalone CPython + app/ 纯源码 + pywebview/WebView2 + 首启下载 onnx 模型 + MinGit + 数据模型分离在 %LOCALAPPDATA%\LocalKB）。
> 调研结论均为 2026 年 7 月当下事实，来源见文末。

## 一句话结论

**不要做单文件 exe（PyInstaller onefile），保持现有目录形态；Windows 发 Inno Setup 安装器 + 便携 zip 双形态；自更新自写（只换 app/，约 200 行）；Mac 出 CI 构建的实验性包、暂不买 $99 开发者账号；大陆分发用 GitHub Release 主源 + 多镜像前缀 + Cloudflare R2 免费第二源。**

---

## 1. 为什么不做 PyInstaller 单文件 exe

- onnxruntime 在 PyInstaller 下的 `DLL load failed` 问题 2025 年仍有新 issue，需要各种 workaround 且不稳定；lancedb/pyarrow 同样有打包断链问题。当前"内嵌 CPython + 源码目录"形态**恰好绕开了全部这些坑**。
- onefile 运行时自解压行为酷似恶意 packer，是杀软误报重灾区（PyInstaller 官方 FAQ 级问题）；`--onedir` 好很多，但那就跟现在的目录形态没区别了，白折腾。
- MSIX 非商店分发必须有受信任证书签名，无证书直接出局。

**结论：现有 bundle 形态就是正确答案，发布层只需要在外面"套壳"。**

## 2. Windows 发布形态（双轨）

### 2a. Inno Setup 安装器（主推，给小白用户）
- 对现有目录零侵入：把 bundle 整个打进去，装到 `%LOCALAPPDATA%\Programs\PaperPiggy`（用户级安装，无需管理员权限），开始菜单/桌面快捷方式指向 LocalKB.vbs 或专用小 exe 启动器。
- 数据本来就在 `%LOCALAPPDATA%\LocalKB`，卸载重装不丢索引/模型 —— 架构天然支持。
- 注意事项：安装器**不要命名为 setup.exe**（加重 Defender 盯梢），填全 Publisher/ProductName/版本元数据；加一段检测注册表无 WebView2 时运行 Evergreen Bootstrapper（约 2MB，随包携带）的脚本 —— Win11 出厂预装，Win10 绝大多数已推送但仍有漏网。
- Inno Setup 支持中文向导，脚本 100 行以内。

### 2b. 便携 zip（保留，给进阶用户/U盘场景）
- 现有 bundle 直接 zip；包内 `portable.txt` 机制已实现（数据落包内）。

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
1. **版本地基**：`config.py` 加 `APP_VERSION = "1.0.0"`（当前全仓库无版本号，仅 mcp_server.py 硬编码 serverInfo 1.2.0，需统一）；build_bundle.py 打包时生成 `app/version.json`（版本+构建日期+app.zip 的 sha256）。
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
2. **v1.0 发布**：build_bundle 产出 → Inno Setup 打包 + 便携 zip → GitHub Release（含 app.zip + sha256）→ R2 同步一份 → README/小红书写清 SmartScreen"仍要运行"步骤。
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
