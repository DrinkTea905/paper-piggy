; ─────────────────────────────────────────────────────────────────────────────
; 论文猪 / PaperPiggy —— Inno Setup 安装脚本
;
; 编译：不要直接用 ISCC 跑这个文件。用 build_installer.py，它会：
;   ① 从 config.APP_VERSION 读版本号（唯一事实源）→ 传 /DAppVersion
;   ② 确认 dist\LocalKB\ 已由 build_bundle.py 构建好
;   ③ 确认包内没有 portable.txt（❗ 见下方 §便携模式）
;   ④ 调 ISCC 出安装器，再打一份便携 zip
;
; 设计要点（改之前先读，每一条都有踩过的坑）：
;
; §零侵入：对 build_bundle.py 产出的目录形态原样打包，不重排结构。
;          app\ 保持明文 .py（本项目开源，不编译不混淆，用户可以直接改）。
;
; §便携模式：包目录里放 portable.txt = 数据/模型写进包内。
;          ⛔ 安装器版**绝不能带 portable.txt** —— 装到 Program Files 后包内不可写，
;             首次建库就会崩。只有便携 zip 才带。
;          安装器版的数据落 %LOCALAPPDATA%\LocalKB（run_localkb.py 自己判定）。
;
; §卸载：只删程序目录，**绝不删 %LOCALAPPDATA%\LocalKB**（用户的索引和文献元数据在那儿，
;        删了就是灾难）。卸载后重装能直接复用原索引。
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

#define AppName        "论文猪"
#define AppNameEn      "PaperPiggy"
#define AppPublisher   "DrinkTea905"
#define AppURL         "https://github.com/DrinkTea905/paper-piggy"
#define AppExeName     "LocalKB.vbs"
#define BundleDir      "..\LocalKB源码\dist\LocalKB"

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
SetupIconFile=..\LocalKB源码\web\PaperPiggy.ico
UninstallDisplayIcon={app}\app\web\PaperPiggy.ico
UninstallDisplayName={#AppName} {#AppVersion}

Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; 装到 Program Files 需要管理员；用户若想免管理员，用便携 zip
PrivilegesRequired=admin
MinVersion=10.0

[Languages]
Name: "chinese"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加选项:"

[Files]
; ── 整个 bundle 原样打包（app\ + python\ + git\ + 启动器）──
; ⚠️ 刻意排除 portable.txt：安装版数据必须落 %LOCALAPPDATA%，见 §便携模式
Source: "{#BundleDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs; \
    Excludes: "portable.txt,*.pdb,__pycache__,data\*,logs\*"

; ── WebView2 Evergreen Bootstrapper（约 2MB，仅在系统缺 WebView2 时运行）──
Source: "MicrosoftEdgeWebview2Setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall; \
    Check: not IsWebView2Installed

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\app\web\PaperPiggy.ico"
Name: "{group}\卸载 {#AppName}";   Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\app\web\PaperPiggy.ico"; Tasks: desktopicon

[Run]
; 缺 WebView2 时静默安装（/silent /install）。装不上也不阻断安装流程——
; 应用会退化成用系统浏览器打开，功能仍可用，只是不是原生窗口。
Filename: "{tmp}\MicrosoftEdgeWebview2Setup.exe"; Parameters: "/silent /install"; \
    StatusMsg: "正在安装 Microsoft Edge WebView2 运行时（窗口渲染所需）..."; \
    Flags: waituntilterminated; Check: not IsWebView2Installed

Filename: "{app}\{#AppExeName}"; Description: "立即启动 {#AppName}"; \
    Flags: postinstall nowait skipifsilent shellexec

[UninstallDelete]
; 程序目录里的运行时垃圾（用户数据不在这儿，在 %LOCALAPPDATA%\LocalKB）
Type: filesandordirs; Name: "{app}\app\__pycache__"
Type: filesandordirs; Name: "{app}\python\Lib\site-packages\__pycache__"

; ⛔ 绝不要在这里写 %LOCALAPPDATA%\LocalKB —— 那是用户的索引和文献元数据。
;    卸载不删数据，重装即可复用。

[Code]
{ 检测 WebView2 Evergreen Runtime 是否已安装。
  微软官方推荐的检测法：查 EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5} 下的 pv 值。
  三个位置都要查：HKLM 的 64/32 位视图 + HKCU（per-user 安装）。 }
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

{ 升级安装时，先确认应用没在跑（否则替换 app\ 会失败） }
function InitializeSetup: Boolean;
begin
  Result := True;
end;
