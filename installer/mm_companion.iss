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
begin
  Result := True;
  PrevInstalled := False;
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
  { A portable install drops a marker beside the exe; the app then keeps its
    workspace in a local "data" folder instead of %APPDATA%. }
  if (CurStep = ssPostInstall) and IsPortable() then
    SaveStringToFile(ExpandConstant('{app}\portable.flag'), '', False);
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
