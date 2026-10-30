; 固定发布载荷的独立管理员安装器。不得读取用户指定源码或执行 PowerShell。
; 目录与文件从第一次创建即为 Administrators owner / 普通用户 RX。
; 只安装新一代受保护组件，保留已有代、用户数据及测试 Disabled 任务供检查。
#ifndef Stage
  #error Build with installer/build_backup_component.py
#endif
#ifndef AppVersion
  #error AppVersion must come from config.APP_VERSION
#endif
[Setup]
AppId=PaperPiggyBackupComponent
AppName=PaperPiggy 备份组件
AppVersion={#AppVersion}
DefaultDirName={commonpf64}\PaperPiggyBackup
CreateAppDir=no
Uninstallable=no
CreateUninstallRegKey=no
#ifdef ABIProbe
PrivilegesRequired=lowest
#else
PrivilegesRequired=admin
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
OutputDir={#Output}
#ifdef ABIProbe
OutputBaseFilename=backup_component_abi_probe
#else
OutputBaseFilename=backup_component
#endif
Compression=lzma2/normal
SolidCompression=no
DisableWelcomePage=yes
DisableDirPage=yes
DisableProgramGroupPage=yes
DisableReadyPage=yes
DisableFinishedPage=yes
CloseApplications=no
RestartApplications=no
AllowCancelDuringInstall=no
SetupLogging=no

[Files]
#ifndef ABIProbe
#include Stage + "\files.iss"
#endif

[Code]
type
  // Inno Setup 6 的 Setup.e32 是 32 位；下列 Win32 指针/HANDLE 固定为 4 字节。
  // build helper 校验 Setup.e32 PE machine，防止换成 64 位引擎后误用此 ABI。
  TSecurityAttributes = record
    Size: LongWord;
    Descriptor: LongWord;
    InheritHandle: LongWord;
  end;
  TTokenBuffer = record
    Sid: LongWord;
    Attributes: LongWord;
    Buffer: array[0..255] of Byte;
  end;

function ConvertSD(SDDL: String; Revision: LongWord; var Descriptor: LongWord; Size: LongWord): Boolean;
  external 'ConvertStringSecurityDescriptorToSecurityDescriptorW@advapi32.dll stdcall';
function MakeDirectory(Name: String; var Security: TSecurityAttributes): Boolean;
  external 'CreateDirectoryW@kernel32.dll stdcall';
function NewFile(Name: String; Access, Share: LongWord; var Security: TSecurityAttributes; Disposition, Flags: LongWord; Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function OpenFile(Name: String; Access, Share: LongWord; Security: LongWord; Disposition, Flags: LongWord; Template: LongWord): LongWord;
  external 'CreateFileW@kernel32.dll stdcall';
function CloseHandle(Handle: LongWord): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';
function FreeMemory(Handle: LongWord): LongWord;
  external 'LocalFree@kernel32.dll stdcall';
function CurrentProcess(): LongWord;
  external 'GetCurrentProcess@kernel32.dll stdcall';
function OpenToken(Process: LongWord; Access: LongWord; var Token: LongWord): Boolean;
  external 'OpenProcessToken@advapi32.dll stdcall';
function GetToken(Token: LongWord; Kind: LongWord; var Buffer: TTokenBuffer; Size: LongWord; var Required: LongWord): Boolean;
  external 'GetTokenInformation@advapi32.dll stdcall';
function GetTokenNumber(Token: LongWord; Kind: LongWord; var Value: LongWord; Size: LongWord; var Required: LongWord): Boolean;
  external 'GetTokenInformation@advapi32.dll stdcall';
function ShellWindow(): LongWord;
  external 'GetShellWindow@user32.dll stdcall';
function WindowProcess(Window: LongWord; var ProcessId: LongWord): LongWord;
  external 'GetWindowThreadProcessId@user32.dll stdcall';
function OpenProcess(Access: LongWord; Inherit: Boolean; ProcessId: LongWord): LongWord;
  external 'OpenProcess@kernel32.dll stdcall';
function SidString(Sid: LongWord; var Text: LongWord): Boolean;
  external 'ConvertSidToStringSidW@advapi32.dll stdcall';
function Impersonate(Token: LongWord): Boolean;
  external 'ImpersonateLoggedOnUser@advapi32.dll stdcall';
function Revert(): Boolean;
  external 'RevertToSelf@advapi32.dll stdcall';
function Attributes(Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';
procedure ExitSelf(Code: LongWord);
  external 'ExitProcess@kernel32.dll stdcall';

var
  DataDir, UserSid, InstanceId, Generation, BaseDir, TargetDir: String;
  Acceptance: Boolean;
  InstanceReady: Boolean;
  Linked: LongWord;

function HexId(Value: String; Count: Integer): Boolean;
var I: Integer;
begin
  Result := Length(Value) = Count;
  for I := 1 to Length(Value) do
    if Pos(Value[I], '0123456789abcdef') = 0 then Result := False;
end;

function TokenSid(Token: LongWord): String;
var Text, Required: LongWord; Buffer: TTokenBuffer;
begin
  if not GetToken(Token, 1, Buffer, SizeOf(Buffer), Required) then RaiseException('Cannot query token identity');
  if not SidString(Buffer.Sid, Text) then RaiseException('Cannot read token SID');
  try Result := CastIntegerToString(LongInt(Text)); finally FreeMemory(Text); end;
end;

function CurrentSid(): String;
var Token: LongWord;
begin
  if not OpenToken(CurrentProcess(),$0008,Token) then RaiseException('Cannot query installer token');
  try Result:=TokenSid(Token);finally CloseHandle(Token);end;
end;

procedure AcquireOrdinaryToken(ExpectedSid: String);
var ProcessId, Process, Current, Required, CurrentSession, Session, Elevated, Kind: LongWord;
begin
  if WindowProcess(ShellWindow(),ProcessId)=0 then RaiseException('No interactive desktop shell');
  Process:=OpenProcess($1000,False,ProcessId);
  if Process=0 then RaiseException('Cannot query desktop process');
  try
    if not OpenToken(Process,$000A,Linked) then RaiseException('Cannot open ordinary desktop token');
  finally CloseHandle(Process);end;
  try
    if not OpenToken(CurrentProcess(),$0008,Current) then RaiseException('Cannot query current session');
    try
      if not GetTokenNumber(Current,12,CurrentSession,4,Required) then RaiseException('Cannot read current session');
    finally CloseHandle(Current);end;
    if not GetTokenNumber(Linked,12,Session,4,Required) or
       not GetTokenNumber(Linked,20,Elevated,4,Required) or
       not GetTokenNumber(Linked,8,Kind,4,Required) then RaiseException('Cannot verify desktop token');
    if (TokenSid(Linked)<>ExpectedSid) or (Session<>CurrentSession) or (Elevated<>0) or (Kind<>1) then
      RaiseException('Desktop must be the same user and session with an ordinary primary token');
  except CloseHandle(Linked);Linked:=0;RaiseException(GetExceptionMessage());end;
end;

procedure NoLinks(Path: String);
var Parent: String; Attr: LongWord;
begin
  while Length(Path) > 3 do begin
    Attr := Attributes(Path);
    if (Attr <> $FFFFFFFF) and ((Attr and $400) <> 0) then RaiseException('Reparse point rejected: '+Path);
    Parent := ExtractFileDir(Path);
    if Parent = Path then Break;
    Path := Parent;
  end;
end;

procedure CheckProtected(Path: String; Directory: Boolean);
var I: Integer; Handle: LongWord; Flags, ErrorCode: LongWord;
  Rights: array[0..6] of LongWord;
begin
  NoLinks(Path);
  Rights[0]:=$2; Rights[1]:=$4; Rights[2]:=$10; Rights[3]:=$100;
  Rights[4]:=$10000; Rights[5]:=$40000; Rights[6]:=$80000;
  Flags := 0;
  if Directory then Flags := $02000000;
  if not Impersonate(Linked) then RaiseException('Cannot check ordinary-user access; error='+IntToStr(DLLGetLastError()));
  try
    for I:=0 to 6 do begin
      Handle := OpenFile(Path, Rights[I], 7, 0, 3, Flags, 0);
      ErrorCode := DLLGetLastError();
      if Handle <> $FFFFFFFF then begin CloseHandle(Handle); RaiseException('User-writable component rejected: '+Path); end;
      if ErrorCode <> 5 then RaiseException('Cannot verify component protection; error='+IntToStr(ErrorCode)+' access='+IntToStr(Rights[I])+': '+Path);
    end;
  finally
    if not Revert() then ExitSelf(74);
  end;
end;

procedure SecureDirectory(Path: String);
var Descriptor: LongWord; SA: TSecurityAttributes; SDDL: String;
begin
  NoLinks(Path);
  if DirExists(Path) then begin CheckProtected(Path, True); Exit; end;
  SDDL := 'O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;'+UserSid+')';
  if not ConvertSD(SDDL, 1, Descriptor, 0) then RaiseException('Cannot create directory security');
  try
    SA.Size:=SizeOf(SA); SA.Descriptor:=Descriptor; SA.InheritHandle:=0;
    if not MakeDirectory(Path, SA) then RaiseException('Cannot atomically create protected directory: '+Path);
  finally FreeMemory(Descriptor); end;
  CheckProtected(Path, True);
end;

procedure InstallFile(Embedded, Relative, Expected: String);
var Destination, Parent, Remaining, Segment: String; Descriptor, Handle: LongWord;
  SA: TSecurityAttributes; Bytes: AnsiString; Separator: Integer;
begin
  Destination := TargetDir+'\'+Relative;
  Remaining := ExtractFileDir(Relative);
  Parent := TargetDir;
  while Remaining <> '' do begin
    Separator:=Pos('\',Remaining);
    if Separator=0 then begin Segment:=Remaining; Remaining:=''; end
    else begin Segment:=Copy(Remaining,1,Separator-1); Delete(Remaining,1,Separator); end;
    if Segment <> '' then begin Parent:=Parent+'\'+Segment; SecureDirectory(Parent); end;
  end;
  if not ConvertSD('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;'+UserSid+')', 1, Descriptor, 0) then
    RaiseException('Cannot create file security');
  try
    SA.Size:=SizeOf(SA); SA.Descriptor:=Descriptor; SA.InheritHandle:=0;
    Handle := NewFile(Destination, $40000000, 0, SA, 1, $80, 0);
    if Handle = $FFFFFFFF then RaiseException('Cannot atomically create protected file: '+Relative);
    CloseHandle(Handle);
  finally FreeMemory(Descriptor); end;
  ExtractTemporaryFile(Embedded);
  if not LoadStringFromFile(ExpandConstant('{tmp}\')+Embedded, Bytes) then RaiseException('Cannot read embedded payload');
  if not SaveStringToFile(Destination, Bytes, False) then RaiseException('Cannot write protected payload');
  CheckProtected(Destination, False);
  if LowerCase(GetSHA256OfFile(Destination)) <> Expected then RaiseException('Payload hash mismatch: '+Relative);
end;

procedure InstallPayload();
var Command: String; ResultCode: Integer;
begin
  DataDir:=ExpandConstant('{param:DATA|}'); UserSid:=ExpandConstant('{param:SID|}');
  InstanceId:=ExpandConstant('{param:INSTANCE|}'); Generation:=ExpandConstant('{param:GENERATION|}');
  Acceptance:=ExpandConstant('{param:ACCEPTANCE|0}')='1';
  if (DataDir='') or (ExtractFileDrive(DataDir)='') or (Copy(DataDir,1,2)='\\') then RaiseException('Use PaperPiggy to select an absolute local data directory');
  if not HexId(InstanceId,20) or not HexId(Generation,64) then RaiseException('Invalid component instance');
  if CurrentSid() <> UserSid then RaiseException('UAC must authorize the same Windows user as PaperPiggy');
  AcquireOrdinaryToken(UserSid);
  try
    NoLinks(DataDir);
    BaseDir:=ExpandConstant('{commonpf64}\PaperPiggyBackup');
    SecureDirectory(BaseDir); SecureDirectory(BaseDir+'\'+InstanceId);
    InstanceReady:=True;
    TargetDir:=BaseDir+'\'+InstanceId+'\'+Generation;
    if DirExists(TargetDir) then RaiseException('Generation already exists; prepare a fresh installation');
    SecureDirectory(TargetDir);
    #ifndef ABIProbe
    #include Stage + "\install.iss"
    #endif
    Command := '-I -S '+AddQuotes(TargetDir+'\code\backup_service.py')+
      ' --register-native --data '+AddQuotes(DataDir)+' --owner-sid '+AddQuotes(UserSid)+
      ' --generation '+Generation;
    if Acceptance then Command:=Command+' --acceptance-once';
    if not Exec(TargetDir+'\runtime\pythonw.exe',Command,TargetDir,SW_HIDE,ewWaitUntilTerminated,ResultCode) or (ResultCode<>0) then
      RaiseException('Protected backup registration failed; existing component retained');
  finally CloseHandle(Linked); end;
end;

procedure SaveNativeFailure(Reason: String);
var Descriptor, Handle: LongWord; SA: TSecurityAttributes; Path: String;
begin
  if not InstanceReady then Exit;
  Path:=BaseDir+'\'+InstanceId+'\native-error-'+Generation+'.txt';
  NoLinks(Path);
  if not ConvertSD('O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;0x1200a9;;;'+UserSid+')',1,Descriptor,0) then Exit;
  try
    SA.Size:=SizeOf(SA);SA.Descriptor:=Descriptor;SA.InheritHandle:=0;
    Handle:=NewFile(Path,$40000000,0,SA,1,$80,0);
    if Handle=$FFFFFFFF then Exit;
    CloseHandle(Handle);
    SaveStringToFile(Path,Reason,False);
  finally FreeMemory(Descriptor);end;
end;

#ifdef ABIProbe
procedure RunABIProbe();
var Path, ActualSid, SDDL: String; Descriptor, Handle: LongWord;
  SA: TSecurityAttributes; Bytes: AnsiString; ProtectedOK: Boolean;
begin
  Path:=ExpandConstant('{param:PROBEOUT|}');
  if (Path='') or DirExists(Path) then RaiseException('Use a fresh ordinary-user ABI fixture directory');
  NoLinks(Path);
  ActualSid:=CurrentSid();
  if Pos('S-1-',ActualSid)<>1 then RaiseException('SID pointer conversion failed');
  if SizeOf(SA)<>12 then RaiseException('Unexpected Inno Win32 SECURITY_ATTRIBUTES ABI');
  SDDL:='O:'+ActualSid+'G:'+ActualSid+'D:P(A;OICI;FA;;;'+ActualSid+')';
  if not ConvertSD(SDDL,1,Descriptor,0) then RaiseException('Probe ConvertSD failed');
  try
    SA.Size:=SizeOf(SA);SA.Descriptor:=Descriptor;SA.InheritHandle:=0;
    if not MakeDirectory(Path,SA) then RaiseException('Probe protected directory creation failed');
    Handle:=NewFile(Path+'\binary.bin',$40000000,0,SA,1,$80,0);
    if Handle=$FFFFFFFF then RaiseException('Probe protected file creation failed');
    CloseHandle(Handle);
  finally FreeMemory(Descriptor);end;
  Bytes:='native ABI'+#0+'binary tail';
  if not SaveStringToFile(Path+'\binary.bin',Bytes,False) then RaiseException('Probe binary write failed');
  ProtectedOK:=False;
  AcquireOrdinaryToken(ActualSid);
  if Linked<>0 then begin
    try
      try
        CheckProtected(ExpandConstant('{commonpf64}\PaperPiggyBackup'),True);
        ProtectedOK:=True;
      except SaveStringToFile(Path+'\protection-check-error.txt',GetExceptionMessage(),False);end;
    finally CloseHandle(Linked);end;
  end;
  Bytes:='{"ok":true,"pointer_bits":32,"security_attributes_size":12,"sid":"'+ActualSid+'","protection_check_passed":';
  if ProtectedOK then Bytes:=Bytes+'true}' else Bytes:=Bytes+'false}';
  if not SaveStringToFile(Path+'\probe.json',Bytes,False) then RaiseException('Probe report failed');
end;
#endif

procedure CurStepChanged(CurStep: TSetupStep);
var Reason: String;
begin
  if CurStep=ssInstall then begin
    #ifdef ABIProbe
    RunABIProbe();
    #else
    try InstallPayload(); except
      Reason:=GetExceptionMessage();
      try SaveNativeFailure(Reason); except Log('Could not persist native installer error'); end;
      RaiseException(Reason);
    end;
    #endif
  end;
end;
