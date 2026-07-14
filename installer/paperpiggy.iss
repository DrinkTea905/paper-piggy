; ─────────────────────────────────────────────────────────────────────────────
; PaperPiggy · 论文小猪 —— Inno Setup 安装脚本
;
; 编译：不要直接用 ISCC 跑这个文件。用 build_installer.py，它会：
;   ① 从 config.APP_VERSION 读版本号（唯一事实源）→ 传 /DAppVersion
;   ② 确认 dist\LocalKB\ 已由 build_bundle.py 构建好
;   ③ 调 ISCC 出安装器（1.0.0 起**不再出便携 zip**，见 §为什么砍掉便携 zip）
;
; 设计要点（改之前先读，每一条都有踩过的坑）：
;
; §零侵入：对 build_bundle.py 产出的目录形态原样打包，不重排结构。
;          app\ 保持明文 .py（本项目开源，不编译不混淆，用户可以直接改）。
;
; §启动器：快捷方式 与「安装完成后启动」都**直接指向 python\pythonw.exe + run_localkb.py**，
;          ⛔ **绝不经过 PaperPiggy.vbs，也绝不加 shellexec**。
;          2026-07-14 真实事故：VBS 里 `s.Run cmd, 0, False` 的那个 0 = SW_HIDE，经由
;          STARTUPINFO.wShowWindow 传给子进程，pywebview 建窗口时继承成「隐藏」——
;          应用**每次都启动成功、但窗口不可见**。用户点一次快捷方式就多一个看不见的
;          幽灵进程（实测攒到 8 个，其中一个占着 8770）。而 启动.bat 走 `start ""`
;          （SW_SHOWNORMAL）所以正常，于是现象是「bat 能启动、桌面快捷方式点了没反应」，
;          极难定位。pythonw.exe 自带无控制台窗口，VBS 那一层从一开始就是多余的。
;
; §数据同目录：安装器**带 portable.txt**（从 installer\portable.txt 装入）
;          → 索引 / 模型 / wiki / 0_Agent* 全部落在安装目录内。用户可以把整个应用装到
;          D:\PaperPiggy，一个文件夹搬走，C 盘一点不占。
;          ⚠️ 这要求安装目录**可写** —— 所以必须配用户级安装（PrivilegesRequired=lowest）。
;             这两个决定是**一套的，别只改一个**：改回 admin+Program Files 而留着 portable.txt，
;             用户首次建库就会崩在「包内不可写」上。
;          兜底：万一用户在向导里把目录改到了 Program Files，config.py 的 _writable() 会探测到
;          并回退 %LOCALAPPDATA%\PaperPiggy —— 占点 C 盘，但不崩。
;
; §为什么砍掉便携 zip：数据既然与程序同目录，「删掉旧文件夹、解压新版」——便携软件最常规的
;          升级姿势——就会把用户的索引、wiki、API key、写好的论文一次性删光。
;          走安装器升级则不会：Inno 只覆盖 app\ 和 python\，不碰 data\ 和 0_Agent*。
;          所以只发安装器，不发 zip。
;
; §卸载：Inno 只删它自己装进去的文件。用户的 data\ / 0_Agent* 会**原样留在安装目录**
;        （卸载器提示「目录非空，未删除」）。这是有意的：卸载后重装可以直接接着用。
;        ⛔ 绝不要在 [UninstallDelete] 里写 {app}\data 或 {app}\0_Agent* ——
;           那是用户的索引、综述和论文，删了就是灾难。
;
; §WebView2：pywebview 靠它渲染窗口。缺了会退化成弹系统浏览器（体验崩坏）。
;           安装器检测注册表，缺失时静默装 Evergreen Bootstrapper（约 2MB，随包带）。
;
; §安装包命名：不叫 setup.exe。SmartScreen 对泛名 setup.exe 的信誉积累更差，
;             且用户下载目录里一堆 setup.exe 根本分不清。
; ─────────────────────────────────────────────────────────────────────────────

#ifndef AppVersion
  #define AppVersion "0.0.0-dev"    ; 兜底值；正常由 build_installer.py 用 /DAppVersion 传入
#endif

#define AppName        "论文小猪"
#define AppNameEn      "PaperPiggy"
#define AppPublisher   "DrinkTea905"
#define AppURL         "https://github.com/DrinkTea905/paper-piggy"
; 快捷方式**直接指向 pythonw.exe**，不经过 PaperPiggy.vbs（见文件头 §启动器：别再走 .vbs）
#define AppLauncher    "python\pythonw.exe"
#define AppScript      "run_localkb.py"
#define BundleDir      "..\src\dist\LocalKB"

[Setup]
; ⛔ AppId 一旦发布就不能再改：改了会被 Windows 当成另一个应用，升级安装会装出两份。
;    这个 GUID 是确定性生成的：uuid5(NAMESPACE_DNS, 'github.com/DrinkTea905/paper-piggy')
AppId={{E7032B45-A391-505C-96E4-3A14095913EB}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} —— 本地文献知识库

DefaultDirName={autopf}\{#AppNameEn}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE
OutputDir=..\dist-installer
; 不叫 setup.exe（见 §安装包命名）
OutputBaseFilename=PaperPiggy-{#AppVersion}-win64
; .ico 由 build_installer.py 的 ensure_icon() 从 web/PaperPiggy.png 现封（仓库里只有 .png）
SetupIconFile=PaperPiggy.ico
UninstallDisplayIcon={app}\PaperPiggy.ico
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

; ★ 用户级安装（不是 Program Files），三个理由，缺一不可：
;   ① 目标用户是法学/社科研究者，很多人用单位配的电脑，**没有管理员权限**；装的时候也不弹 UAC。
;   ② 数据与程序同目录（见 §数据同目录）—— Program Files 普通权限不可写，首次建库就会崩。
;   ③ 将来接自动更新时，updater 要能改写自己的程序目录；装在 Program Files 里第一步就 PermissionError。
;   lowest 之下 {autopf} 自动解析为 %LOCALAPPDATA%\Programs（不再是 C:\Program Files）。
;   安装向导的「选择安装位置」页**刻意保留**：包 800M + 模型 1~2G + 索引若干 G，
;   用户想整个装到 D:\PaperPiggy 就该让他装。
PrivilegesRequired=lowest
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"

[Files]
; ── 整个 bundle 原样打包（app\ + python\ + git\ + 启动器）──
; ⚠️ Excludes 是**隐私闸门**，不是优化：开发机自测 bundle 时，data\ 里躺着真实文献元数据和
;    硅基流动 API key，0_Agent* 里躺着交付物 —— 漏进公开安装包就是一次数据泄漏。
;    （build_bundle.py 已经不往 dist 里放这些，但护栏要有两道。）
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
    Excludes: "portable.txt,*.pdb,__pycache__,data\*,logs\*,update\*,0_Agent交付物\*,0_Agent资料库\*"

; ── 数据同目录开关（见 §数据同目录）──
; 从 installer\portable.txt 装进去，而不是让打包脚本临时生成 —— 少一个「忘了生成」的失败模式。
; onlyifdoesntexist：用户要是自己删了它（想把数据挪回 C 盘），升级安装别给他装回来。
Source: "portable.txt"; DestDir: "{app}"; Flags: onlyifdoesntexist

; ── 应用图标（快捷方式 + 卸载项都指向它）──
Source: "PaperPiggy.ico"; DestDir: "{app}"; Flags: ignoreversion

; ── WebView2 Evergreen Bootstrapper（约 2MB，仅在系统缺 WebView2 时运行）──
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; \
    Check: not IsWebView2Installed

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppLauncher}"; Parameters: """{app}\{#AppScript}"""; \
    WorkingDir: "{app}"; IconFilename: "{app}\PaperPiggy.ico"
Name: "{group}\卸载 {#AppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppLauncher}"; Parameters: """{app}\{#AppScript}"""; \
    WorkingDir: "{app}"; IconFilename: "{app}\PaperPiggy.ico"; Tasks: desktopicon

[Run]
; 缺 WebView2 时静默安装（/silent /install）。装不上也不阻断安装流程——
; 应用会退化成用系统浏览器打开，功能仍可用，只是不是原生窗口。
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "正在安装 Microsoft Edge WebView2 运行时（窗口渲染所需）..."; \
    Flags: waituntilterminated; Check: not IsWebView2Installed

; ⚠️ 不要加 shellexec：那会经由 shell 打开文件（对 .vbs 就是交给 WScript），
;    直接 CreateProcess 起 pythonw.exe 才能保证窗口正常显示。
Filename: "{app}\{#AppLauncher}"; Parameters: """{app}\{#AppScript}"""; WorkingDir: "{app}"; \
    Description: "立即启动 {#AppName}"; Flags: postinstall nowait skipifsilent

[UninstallDelete]
; 只清运行时垃圾（编译缓存）。
Type: filesandordirs; Name: "{app}\app\__pycache__"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"

; ⛔ 绝不要在这里写 {app}\data、{app}\0_Agent交付物、{app}\0_Agent资料库、
;    也不要写 %LOCALAPPDATA%\PaperPiggy —— 那是用户的索引、文献元数据、综述 wiki
;    和写好的论文。卸载不删数据，重装即可接着用。
;    （数据与程序同目录，见文件头 §数据同目录 —— 正因如此，这一段更要克制。）

[Code]
// 检测 WebView2 Evergreen Runtime 是否已安装。
// 微软官方推荐的检测法：查 EdgeUpdate\Clients\<那个固定 GUID> 下的 pv 值。
// 三个位置都要查：HKLM 的 64/32 位视图 + HKCU（per-user 安装）。
//
// ⚠️ 这里必须用 // 行注释，不能用 Pascal 的 { } 块注释 ——
//    GUID 里的 '}' 会提前把块注释关掉，报 "Syntax error"（踩过）。
function IsWebView2Installed: Boolean;
var
  pv: String;
begin
  Result := False;
  if RegQueryStringValue(HKEY_LOCAL_MACHINE,
      'SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
      'pv', pv) then
    if (pv <> '') and (pv <> '0.0.0.0') then
      Result := True;

  if not Result then
    if RegQueryStringValue(HKEY_LOCAL_MACHINE,
        'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'pv', pv) then
      if (pv <> '') and (pv <> '0.0.0.0') then
        Result := True;

  if not Result then
    if RegQueryStringValue(HKEY_CURRENT_USER,
        'SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}',
        'pv', pv) then
      if (pv <> '') and (pv <> '0.0.0.0') then
        Result := True;
end;

// 升级安装时的预留钩子（如需检测应用是否在跑，在这里加）
function InitializeSetup: Boolean;
begin
  Result := True;
end;
