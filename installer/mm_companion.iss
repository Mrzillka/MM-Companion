; Inno Setup script for MM-Companion.
;
; Wraps the PyInstaller output (installer/build.ps1 builds both a one-folder and
; a one-file "portable" payload first) into a single shareable Setup exe.
;
; Behaviour:
;   * Fresh machine  -> pick an install dir, optional desktop shortcut, optional
;                       Portable install.
;   * Already installed (detected via the registry uninstall key) -> a custom
;                       page offers Upgrade / Reinstall / Remove. "Upgrade" only
;                       appears when the installed version is older than this one.
;   * Remove         -> runs the app's uninstaller; a checkbox additionally wipes
;                       the user workspace at %APPDATA%\MM-Companion.
;   * In-app update  -> the app runs this silently (core/updates.py) with its
;                       own switches:
;                       /CANCELFILE=<path>  if it exists when Setup starts, the
;                           app gave up waiting for the permission prompt: leave.
;                       /READYFILE=<path>   created once Setup is running (and so
;                           elevated) - the app's cue to quit.
;                       /WAITPID=<pid>[,<pid>]  waited out before anything is
;                           touched, since the running app holds its exe open.
;                       /RELAUNCH=1  start the app again at the end, whether or
;                           not the install worked, so a failure is reported by
;                           the app rather than by it vanishing. The /LOG it passes
;                           records why.
;
; The version is supplied by the build script:  ISCC /DAppVersion=0.1.0 ...

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName "MM-Companion"
#define AppExeName "MM-Companion.exe"
#define AppPublisher "Mrzillka"
; Fixed GUID — must stay constant across releases for upgrade detection to work.
#define AppId "{{4E9C2EF5-C7BD-400C-82E3-72F36FF6DF14}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; Machine-wide install into C:\Program Files — requires (and prompts for) admin.
PrivilegesRequired=admin
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
SetupIconFile=..\src\mm_companion\ui\assets\mm.ico
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName}
OutputDir=output
OutputBaseFilename={#AppName}-Setup-{#AppVersion}

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "portable"; Description: "Portable install (data kept next to the app; not recommended)"; GroupDescription: "Advanced:"; Flags: unchecked

[Files]
; Standard one-folder payload (default).
Source: "..\dist\MM-Companion\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion; Check: not IsPortable
; Portable single-exe payload (only when the Portable task is selected).
Source: "..\dist\MM-Companion-portable.exe"; DestDir: "{app}"; DestName: "{#AppExeName}"; Flags: ignoreversion; Check: IsPortable

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

[Code]
const
  SYNCHRONIZE      = $00100000;
  WAIT_OBJECT_0    = 0;
  WAIT_PID_TIMEOUT = 30000;  { ms per process; the app quits in well under that }

  ACTION_INSTALL   = 0;  { no prior install }
  ACTION_UPGRADE   = 1;
  ACTION_REINSTALL = 2;
  ACTION_REMOVE    = 3;

var
  PrevInstalled: Boolean;
  PrevVersion: String;
  PrevLocation: String;
  PrevUninstaller: String;
  HasUpgradeOption: Boolean;
  ActionPage: TInputOptionWizardPage;
  DelDataCheck: TNewCheckBox;
  AllowSilentCancel: Boolean;
  { In-app update: past the cancel check (the app is quitting for us), whether the
    install ran to the end, and whether the app is still running regardless. }
  Handshaken: Boolean;
  InstallFinished: Boolean;
  AppStillRunning: Boolean;

function OpenProcess(dwDesiredAccess: DWORD; bInheritHandle: BOOL; dwProcessId: DWORD): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(hHandle: THandle; dwMilliseconds: DWORD): DWORD;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(hObject: THandle): BOOL;
  external 'CloseHandle@kernel32.dll stdcall';

function ShouldRelaunch(): Boolean;
begin
  Result := ExpandConstant('{param:RELAUNCH|0}') = '1';
end;

{ Wait for each process in the comma-separated /WAITPID list to exit. One that
  has already gone (or never existed) opens as 0 and is skipped. False if one
  was still running when its time ran out. }
function WaitForProcesses(pids: String): Boolean;
var
  p: Integer;
  pid: String;
  handle: THandle;
begin
  Result := True;
  while pids <> '' do
  begin
    p := Pos(',', pids);
    if p > 0 then begin pid := Copy(pids, 1, p - 1); Delete(pids, 1, p); end
    else begin pid := pids; pids := ''; end;
    handle := OpenProcess(SYNCHRONIZE, False, StrToIntDef(Trim(pid), 0));
    if handle = 0 then
      Log('In-app update: process ' + pid + ' has already exited.')
    else
    begin
      if WaitForSingleObject(handle, WAIT_PID_TIMEOUT) = WAIT_OBJECT_0 then
        Log('In-app update: process ' + pid + ' exited.')
      else
      begin
        Log('In-app update: process ' + pid + ' was still running after ' +
            IntToStr(WAIT_PID_TIMEOUT div 1000) + ' seconds.');
        Result := False;
      end;
      CloseHandle(handle);
    end;
  end;
end;

{ Start the app again as the user who started Setup - not the elevated account,
  which may be a different user with a different workspace. Whatever is in the
  install folder: the new version if the install finished, the old (or what is
  left of it) if not. }
procedure RelaunchApp();
var
  exe: String;
  rc: Integer;
begin
  exe := '';
  try
    exe := ExpandConstant('{app}\{#AppExeName}');
  except
    exe := '';
  end;
  if ((exe = '') or not FileExists(exe)) and (PrevLocation <> '') then
    exe := AddBackslash(RemoveQuotes(PrevLocation)) + '{#AppExeName}';
  if (exe = '') or not FileExists(exe) then
  begin
    Log('In-app update: no app to relaunch.');
    Exit;
  end;
  if ExecAsOriginalUser(exe, '', '', SW_SHOWNORMAL, ewNoWait, rc) then
    Log('In-app update: relaunched ' + exe)
  else
    Log('In-app update: could not relaunch ' + exe + ': ' + SysErrorMessage(rc));
end;

function UninstallKey(): String;
begin
  Result := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#AppId}_is1';
end;

function IsPortable(): Boolean;
begin
  Result := WizardIsTaskSelected('portable');
end;

{ Compare two dotted versions component-wise: -1 if a<b, 0 if equal, 1 if a>b.
  Handles differing part counts (0.1 vs 0.1.0 compare equal). }
function CompareVersions(a, b: String): Integer;
var
  ai, bi, pa, pb: Integer;
begin
  Result := 0;
  while ((a <> '') or (b <> '')) and (Result = 0) do
  begin
    pa := Pos('.', a);
    if pa > 0 then begin ai := StrToIntDef(Copy(a, 1, pa - 1), 0); Delete(a, 1, pa); end
    else begin ai := StrToIntDef(a, 0); a := ''; end;
    pb := Pos('.', b);
    if pb > 0 then begin bi := StrToIntDef(Copy(b, 1, pb - 1), 0); Delete(b, 1, pb); end
    else begin bi := StrToIntDef(b, 0); b := ''; end;
    if ai < bi then Result := -1
    else if ai > bi then Result := 1;
  end;
end;

function InitializeSetup(): Boolean;
var
  readyFile, cancelFile: String;
begin
  Result := True;
  PrevInstalled := False;

  { An in-app update. The cancel check comes strictly before the ready file: the
    app relies on that order to tell "withdrawn in time" from "too late". }
  cancelFile := ExpandConstant('{param:CANCELFILE|}');
  if (cancelFile <> '') and FileExists(cancelFile) then
  begin
    Log('In-app update: the app withdrew before Setup started; nothing done.');
    Result := False;
    Exit;
  end;
  readyFile := ExpandConstant('{param:READYFILE|}');
  if readyFile <> '' then
  begin
    SaveStringToFile(readyFile, 'ready', False);
    Handshaken := True;
    Log('In-app update from the app: waiting for it to close.');
  end;
  { Let the app finish quitting before anything tries to replace its files - and
    if it will not, replace nothing: a half-replaced install is worse than none. }
  if not WaitForProcesses(ExpandConstant('{param:WAITPID|}')) then
  begin
    Log('In-app update: MM-Companion did not close, so nothing was installed.');
    AppStillRunning := True;
    Result := False;
    Exit;
  end;

  { Admin install records the uninstall key under HKLM (64-bit view in 64-bit
    install mode); fall back to HKCU so an older per-user install is still
    detected for upgrade. }
  if RegQueryStringValue(HKLM64, UninstallKey(), 'DisplayVersion', PrevVersion) or
     RegQueryStringValue(HKCU, UninstallKey(), 'DisplayVersion', PrevVersion) then
  begin
    PrevInstalled := True;
    if not RegQueryStringValue(HKLM64, UninstallKey(), 'InstallLocation', PrevLocation) then
      if not RegQueryStringValue(HKCU, UninstallKey(), 'InstallLocation', PrevLocation) then
        PrevLocation := '';
    if not RegQueryStringValue(HKLM64, UninstallKey(), 'UninstallString', PrevUninstaller) then
      if not RegQueryStringValue(HKCU, UninstallKey(), 'UninstallString', PrevUninstaller) then
        PrevUninstaller := '';
  end;
end;

function SelectedAction(): Integer;
begin
  if not PrevInstalled then
  begin
    Result := ACTION_INSTALL;
    Exit;
  end;
  if HasUpgradeOption then
  begin
    case ActionPage.SelectedValueIndex of
      0: Result := ACTION_UPGRADE;
      1: Result := ACTION_REINSTALL;
    else
      Result := ACTION_REMOVE;
    end;
  end
  else
  begin
    if ActionPage.SelectedValueIndex = 0 then Result := ACTION_REINSTALL
    else Result := ACTION_REMOVE;
  end;
end;

procedure ActionRadioClicked(Sender: TObject);
begin
  DelDataCheck.Enabled := (SelectedAction() = ACTION_REMOVE);
  if not DelDataCheck.Enabled then
    DelDataCheck.Checked := False;
end;

procedure InitializeWizard();
begin
  if not PrevInstalled then
    Exit;

  HasUpgradeOption := CompareVersions(PrevVersion, '{#AppVersion}') < 0;

  ActionPage := CreateInputOptionPage(
    wpWelcome,
    'Existing installation found',
    'MM-Companion ' + PrevVersion + ' is already installed on this computer.',
    'Choose what you would like to do, then click Next:',
    True,   { exclusive radio buttons }
    False);

  if HasUpgradeOption then
    ActionPage.Add('Upgrade to version {#AppVersion}');
  ActionPage.Add('Reinstall version {#AppVersion}');
  ActionPage.Add('Remove MM-Companion from this computer');
  ActionPage.SelectedValueIndex := 0;

  DelDataCheck := TNewCheckBox.Create(ActionPage);
  DelDataCheck.Parent := ActionPage.Surface;
  DelDataCheck.Caption := 'Also delete my characters, mods and settings (%APPDATA%\MM-Companion)';
  DelDataCheck.Left := ActionPage.CheckListBox.Left;
  DelDataCheck.Top := ActionPage.CheckListBox.Top + ActionPage.CheckListBox.Height + ScaleY(10);
  DelDataCheck.Width := ActionPage.SurfaceWidth;
  DelDataCheck.Enabled := False;

  ActionPage.CheckListBox.OnClickCheck := @ActionRadioClicked;
end;

procedure RunUninstaller(deleteData: Boolean);
var
  fileName, params: String;
  rc: Integer;
begin
  if PrevUninstaller = '' then
    Exit;
  fileName := RemoveQuotes(PrevUninstaller);
  params := '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART';
  if deleteData then params := params + ' /DELDATA=1' else params := params + ' /DELDATA=0';
  Exec(fileName, params, '', SW_HIDE, ewWaitUntilTerminated, rc);
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
  if PrevInstalled and (CurPageID = ActionPage.ID) then
  begin
    case SelectedAction() of
      ACTION_REMOVE:
        begin
          if MsgBox('Remove MM-Companion from this computer?', mbConfirmation, MB_YESNO) = IDNO then
          begin
            Result := False;
            Exit;
          end;
          RunUninstaller(DelDataCheck.Checked);
          MsgBox('MM-Companion is being removed.', mbInformation, MB_OK);
          AllowSilentCancel := True;
          WizardForm.Close;
          Result := False;
        end;
      ACTION_UPGRADE, ACTION_REINSTALL:
        begin
          { Install over the existing location instead of asking again. }
          if PrevLocation <> '' then
            WizardForm.DirEdit.Text := RemoveBackslashUnlessRoot(PrevLocation);
        end;
    end;
  end;
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := False;
  { Upgrade/Reinstall reuse the recorded location; only a genuinely fresh
    install shows the directory chooser. }
  if PrevInstalled and (PageID = wpSelectDir) and (PrevLocation <> '') then
    Result := True;
end;

procedure CancelButtonClick(CurPageID: Integer; var Cancel, Confirm: Boolean);
begin
  if AllowSilentCancel then
    Confirm := False;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssDone then
    InstallFinished := True;
  { A portable install drops a marker beside the exe; the app then keeps its
    workspace in a local "data" folder instead of %APPDATA%. }
  if (CurStep = ssPostInstall) and IsPortable() then
    SaveStringToFile(ExpandConstant('{app}\portable.flag'), '', False);
end;

procedure DeinitializeSetup();
begin
  if not Handshaken then
    Exit;
  if InstallFinished then
    Log('In-app update: installed version {#AppVersion}.')
  else
    Log('In-app update: Setup ended before the install finished - see above for why.');
  if ShouldRelaunch() and not AppStillRunning then
    RelaunchApp();
end;

{ ------------------------- Uninstaller ------------------------- }

function UninstShouldDeleteData(): Boolean;
begin
  if UninstallSilent() then
    Result := ExpandConstant('{param:DELDATA|0}') = '1'
  else
    Result := MsgBox(
      'Also delete your MM-Companion characters, mods and settings' + #13#10 +
      '(%APPDATA%\MM-Companion)?' + #13#10#13#10 +
      'Choose No to keep them for a future reinstall.',
      mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
  begin
    if UninstShouldDeleteData() then
    begin
      // Normal install keeps data in %APPDATA%; a portable install keeps it in
      // the app dir's "data" folder. Clear whichever exists.
      DelTree(ExpandConstant('{userappdata}\MM-Companion'), True, True, True);
      DelTree(ExpandConstant('{app}\data'), True, True, True);
    end;
  end;
end;
