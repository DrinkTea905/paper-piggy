// PaperPiggy installer preflight.
// Read-only WMI inspection: never terminates, suspends, or modifies a process.

const
  PaperPiggyScanClear = 0;
  PaperPiggyScanBlocked = 1;
  PaperPiggyScanError = 2;

function VariantText(Value: Variant): String;
begin
  try
    Result := Value;
  except
    Result := '';
  end;
end;

function VariantInt(Value: Variant): Integer;
begin
  try
    Result := Value;
  except
    Result := 0;
  end;
end;

function SamePath(const LeftPath, RightPath: String): Boolean;
begin
  Result := CompareText(LeftPath, RightPath) = 0;
end;

function CommandStartsWithInterpreter(const CommandLine, InterpreterPath: String): Boolean;
var
  Command, Quoted: String;
begin
  Command := Trim(CommandLine);
  Quoted := '"' + InterpreterPath + '"';
  Result := (CompareText(Copy(Command, 1, Length(Quoted)), Quoted) = 0) or
    (CompareText(Copy(Command, 1, Length(InterpreterPath)), InterpreterPath) = 0);
end;

function IsKnownPaperPiggyInterpreter(const Name, ExecutablePath, CommandLine,
  PythonExe, PythonwExe: String): Boolean;
begin
  Result := False;
  if (CompareText(Name, 'python.exe') <> 0) and
      (CompareText(Name, 'pythonw.exe') <> 0) then
    Exit;

  if (ExecutablePath <> '') and
      (SamePath(ExecutablePath, PythonExe) or SamePath(ExecutablePath, PythonwExe)) then
  begin
    Result := True;
    Exit;
  end;

  // Some locked-down WMI configurations hide ExecutablePath.  CommandLine is a
  // safe fallback only when it starts with the exact bundled interpreter path.
  Result := CommandStartsWithInterpreter(CommandLine, PythonExe) or
    CommandStartsWithInterpreter(CommandLine, PythonwExe);
end;

function FriendlyClientName(const ProcessName, CommandLine: String): String;
var
  NameLower, CommandLower: String;
begin
  NameLower := Lowercase(ProcessName);
  CommandLower := Lowercase(CommandLine);
  Result := '';
  if (NameLower = 'codex.exe') or (NameLower = 'chatgpt.exe') or
      (Pos('@openai/codex', CommandLower) > 0) then
    Result := 'Codex'
  else if (NameLower = 'claude.exe') or
      (Pos('@anthropic-ai/claude-code', CommandLower) > 0) then
    Result := 'Claude'
  else if NameLower = 'code.exe' then
    Result := 'Visual Studio Code'
  else if NameLower = 'cursor.exe' then
    Result := 'Cursor';
end;

function DescribeMcpOwner(Wmi: Variant; ParentId: Integer;
  const ChildCreation: String): String;
var
  Depth, CurrentId: Integer;
  Parent: Variant;
  ParentName, ParentCommand, ParentCreation, CurrentCreation, Chain, Friendly: String;
begin
  Result := '';
  Chain := '';
  CurrentId := ParentId;
  CurrentCreation := ChildCreation;

  for Depth := 0 to 7 do
  begin
    if CurrentId <= 0 then
      Break;
    try
      Parent := Wmi.Get('Win32_Process.Handle="' + IntToStr(CurrentId) + '"');
      ParentName := VariantText(Parent.Name);
      ParentCommand := VariantText(Parent.CommandLine);
      ParentCreation := VariantText(Parent.CreationDate);
    except
      Break;
    end;

    // A newer process cannot be this child's real parent: the PID was reused.
    if (CurrentCreation <> '') and (ParentCreation <> '') and
        (CompareText(ParentCreation, CurrentCreation) > 0) then
      Break;

    if Chain = '' then
      Chain := ParentName
    else
      Chain := Chain + ' ← ' + ParentName;
    Friendly := FriendlyClientName(ParentName, ParentCommand);
    if Friendly <> '' then
    begin
      Result := Friendly;
      Exit;
    end;

    CurrentCreation := ParentCreation;
    CurrentId := VariantInt(Parent.ParentProcessId);
  end;

  if Chain <> '' then
    Result := Chain
  else
    Result := '其他 AI 客户端';
end;

// 0=clear, 1=blocking process found, 2=WMI inspection failed.
function ScanPaperPiggyProcesses(const InstallRoot: String;
  var Details, ErrorText: String): Integer;
var
  PythonExe, PythonwExe, McpScript, Name, ExecutablePath, CommandLine,
    CreationDate, Owner, Line: String;
  Locator, Wmi, Processes, ProcessItem: Variant;
  I, Pid, ParentPid, BlockingCount: Integer;
  IsMcp: Boolean;
begin
  Result := PaperPiggyScanClear;
  Details := '';
  ErrorText := '';
  PythonExe := AddBackslash(InstallRoot) + 'python\python.exe';
  PythonwExe := AddBackslash(InstallRoot) + 'python\pythonw.exe';

  // New installation: there cannot be a process using this not-yet-installed runtime.
  if not FileExists(PythonExe) and not FileExists(PythonwExe) then
    Exit;

  McpScript := AddBackslash(InstallRoot) + 'app\mcp_server.py';
  BlockingCount := 0;
  try
    Locator := CreateOleObject('WbemScripting.SWbemLocator');
    Wmi := Locator.ConnectServer('.', 'root\CIMV2');
    Processes := Wmi.ExecQuery(
      'SELECT ProcessId, ParentProcessId, Name, ExecutablePath, CommandLine, CreationDate ' +
      'FROM Win32_Process WHERE Name="python.exe" OR Name="pythonw.exe"');

    for I := 0 to Processes.Count - 1 do
    begin
      ProcessItem := Processes.ItemIndex(I);
      Name := VariantText(ProcessItem.Name);
      ExecutablePath := VariantText(ProcessItem.ExecutablePath);
      CommandLine := VariantText(ProcessItem.CommandLine);
      if not IsKnownPaperPiggyInterpreter(Name, ExecutablePath, CommandLine,
          PythonExe, PythonwExe) then
        Continue;

      Pid := VariantInt(ProcessItem.ProcessId);
      ParentPid := VariantInt(ProcessItem.ParentProcessId);
      CreationDate := VariantText(ProcessItem.CreationDate);
      IsMcp := Pos(Lowercase(McpScript), Lowercase(CommandLine)) > 0;
      if IsMcp then
      begin
        Owner := DescribeMcpOwner(Wmi, ParentPid, CreationDate);
        Line := Format('• %s（PaperPiggy MCP，PID %d）', [Owner, Pid]);
      end
      else
        Line := Format('• PaperPiggy 桌面或后台进程（PID %d）', [Pid]);

      if Details <> '' then
        Details := Details + #13#10;
      Details := Details + Line;
      BlockingCount := BlockingCount + 1;
    end;
  except
    ErrorText :=
      '安装器无法确认 PaperPiggy 是否仍被后台进程使用。' + #13#10 +
      '为避免覆盖正在使用的程序文件，本次安装已暂停。请完全退出 PaperPiggy、Codex、Claude、' +
      'Visual Studio Code 和 Cursor 后重试。';
    Result := PaperPiggyScanError;
    Exit;
  end;

  if BlockingCount > 0 then
    Result := PaperPiggyScanBlocked;
end;
