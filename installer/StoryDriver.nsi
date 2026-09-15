Unicode True
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "StrFunc.nsh"
!include "x64.nsh"
${StrRep}

!ifndef ROOT_DIR
!define ROOT_DIR "${__FILEDIR__}\.."
!endif
!define PRODUCT_NAME "StoryDriver"
!ifndef PRODUCT_VERSION
!define PRODUCT_VERSION "1.0.0"
!endif
!define PRODUCT_PUBLISHER "StoryDriver"
!define PRODUCT_REGKEY "Software\StoryDriver"
!define UNINSTALL_REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\StoryDriver"
!define PAYLOAD_DIR "${ROOT_DIR}\build\native\app"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "${ROOT_DIR}\release\StoryDriver-Setup-x64.exe"
InstallDir "$LOCALAPPDATA\Programs\StoryDriver"
InstallDirRegKey HKCU "${PRODUCT_REGKEY}" "InstallRoot"
Icon "${ROOT_DIR}\assets\desktop\storydriver.ico"
UninstallIcon "${ROOT_DIR}\assets\desktop\storydriver.ico"
BrandingText "StoryDriver - local and private"

Var DataRoot
Var DataRootJson
Var DataRootField
Var BrowseButton
Var LanEnabled
Var LanCheckbox
Var DesktopShortcut
Var DesktopCheckbox
Var NoShortcuts

!define MUI_ABORTWARNING
!define MUI_FINISHPAGE_RUN "$INSTDIR\StoryDriver.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Open StoryDriver"
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_DIRECTORY
Page custom DataOptionsCreate DataOptionsLeave
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_UNPAGE_FINISH
!insertmacro MUI_LANGUAGE "English"

Function .onInit
  ${IfNot} ${RunningX64}
    MessageBox MB_ICONSTOP "StoryDriver requires 64-bit Windows 10 or Windows 11."
    Abort
  ${EndIf}
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\StoryDriver.Desktop.Instance") p .r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_ICONSTOP "Quit StoryDriver from its tray menu before installing an update."
    Abort
  ${EndIf}
  StrCpy $DataRoot "$LOCALAPPDATA\StoryDriver"
  StrCpy $LanEnabled "false"
  StrCpy $DesktopShortcut "false"
  StrCpy $NoShortcuts "false"

  ReadRegStr $0 HKCU "${PRODUCT_REGKEY}" "DataRoot"
  ${If} $0 != ""
    StrCpy $DataRoot $0
  ${EndIf}
  ReadRegStr $0 HKCU "${PRODUCT_REGKEY}" "LanEnabled"
  ${If} $0 == "true"
    StrCpy $LanEnabled "true"
  ${EndIf}

  ${GetParameters} $0
  ${GetOptions} $0 "/DATAROOT=" $1
  ${If} $1 != ""
    StrCpy $DataRoot $1
  ${EndIf}
  ${GetOptions} $0 "/LAN=" $1
  ${If} $1 == "1"
    StrCpy $LanEnabled "true"
  ${ElseIf} $1 == "0"
    StrCpy $LanEnabled "false"
  ${EndIf}
  ${GetOptions} $0 "/DESKTOP=" $1
  ${If} $1 == "1"
    StrCpy $DesktopShortcut "true"
  ${EndIf}
  ClearErrors
  ${GetOptions} $0 "/NOSHORTCUTS" $1
  ${IfNot} ${Errors}
    StrCpy $NoShortcuts "true"
  ${EndIf}
FunctionEnd

Function DataOptionsCreate
  IfFileExists "$INSTDIR\storydriver.config.json" 0 +2
    Abort
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  !insertmacro MUI_HEADER_TEXT "Local data" "Choose where StoryDriver stores writing and generated media."
  ${NSD_CreateLabel} 0 0 100% 26u "This folder is separate from the application so upgrades and uninstall can preserve it."
  Pop $0
  ${NSD_CreateText} 0 32u 78% 13u "$DataRoot"
  Pop $DataRootField
  ${NSD_CreateBrowseButton} 80% 31u 20% 15u "Browse..."
  Pop $BrowseButton
  ${NSD_OnClick} $BrowseButton BrowseDataFolder
  ${NSD_CreateCheckbox} 0 62u 100% 12u "Allow phone access on the private LAN"
  Pop $LanCheckbox
  ${If} $LanEnabled == "true"
    ${NSD_Check} $LanCheckbox
  ${EndIf}
  ${NSD_CreateCheckbox} 0 84u 100% 12u "Create a desktop shortcut"
  Pop $DesktopCheckbox
  ${If} $DesktopShortcut == "true"
    ${NSD_Check} $DesktopCheckbox
  ${EndIf}
  ${NSD_CreateLabel} 0 109u 100% 28u "StoryDriver never enables port forwarding. Windows may request private-network firewall permission when LAN access is first used."
  Pop $0
  nsDialogs::Show
FunctionEnd

Function BrowseDataFolder
  nsDialogs::SelectFolderDialog "Choose the StoryDriver data folder" "$DataRoot"
  Pop $0
  ${If} $0 != error
    ${NSD_SetText} $DataRootField "$0"
  ${EndIf}
FunctionEnd

Function DataOptionsLeave
  ${NSD_GetText} $DataRootField $DataRoot
  ${If} $DataRoot == ""
    MessageBox MB_ICONEXCLAMATION "Choose a data folder."
    Abort
  ${EndIf}
  ${NSD_GetState} $LanCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $LanEnabled "true"
  ${Else}
    StrCpy $LanEnabled "false"
  ${EndIf}
  ${NSD_GetState} $DesktopCheckbox $0
  ${If} $0 == ${BST_CHECKED}
    StrCpy $DesktopShortcut "true"
  ${Else}
    StrCpy $DesktopShortcut "false"
  ${EndIf}
FunctionEnd

Section "StoryDriver" SEC_CORE
  SectionIn RO
  SetShellVarContext current
  ${If} $INSTDIR == ""
  ${OrIf} $DataRoot == ""
    MessageBox MB_ICONSTOP "Application and data folders must not be empty."
    SetErrorLevel 2
    Quit
  ${EndIf}
  ; NSIS GetFullPathName can return empty for a directory that does not exist yet.
  System::Call 'kernel32::GetFullPathNameW(w "$INSTDIR", i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r2'
  ${If} $2 == 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
  ${OrIf} $1 == ""
    MessageBox MB_ICONSTOP "The application folder could not be resolved. Choose another folder."
    SetErrorLevel 2
    Quit
  ${EndIf}
  StrCpy $INSTDIR $1
  System::Call 'kernel32::GetFullPathNameW(w "$DataRoot", i ${NSIS_MAX_STRLEN}, w .r1, p 0) i .r2'
  ${If} $2 == 0
  ${OrIf} $2 >= ${NSIS_MAX_STRLEN}
  ${OrIf} $1 == ""
    MessageBox MB_ICONSTOP "The data folder could not be resolved. Choose another folder."
    SetErrorLevel 2
    Quit
  ${EndIf}
  StrCpy $DataRoot $1
  ${GetRoot} "$INSTDIR" $0
  ${If} $INSTDIR == ""
  ${OrIf} $INSTDIR == "$0"
  ${OrIf} $INSTDIR == "$0\"
  ${OrIf} $INSTDIR == "$WINDIR"
  ${OrIf} $INSTDIR == "$PROGRAMFILES"
  ${OrIf} $INSTDIR == "$LOCALAPPDATA"
  ${OrIf} $INSTDIR == "$PROFILE"
  ${OrIf} $INSTDIR == "$DataRoot"
    MessageBox MB_ICONSTOP "Choose a dedicated StoryDriver application folder, separate from your data folder."
    Abort
  ${EndIf}
  ${GetRoot} "$DataRoot" $0
  ${If} $DataRoot == ""
  ${OrIf} $DataRoot == "$0"
  ${OrIf} $DataRoot == "$0\"
  ${OrIf} $DataRoot == "$WINDIR"
  ${OrIf} $DataRoot == "$PROFILE"
    MessageBox MB_ICONSTOP "Choose a dedicated StoryDriver data folder, not a drive or Windows folder."
    Abort
  ${EndIf}
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*.*"
  IfFileExists "$INSTDIR\StoryDriver.exe" 0 install_failed
  IfFileExists "$INSTDIR\backend\StoryDriverBackend.exe" 0 install_failed
  IfFileExists "$INSTDIR\runtimes\kokoro\StoryDriverNarration.exe" 0 install_failed
  Goto install_verified
  install_failed:
  MessageBox MB_ICONSTOP "Application files could not be installed. Check the destination and available disk space."
  SetErrorLevel 3
  Quit
  install_verified:

  CreateDirectory "$DataRoot"
  CreateDirectory "$DataRoot\logs"
  CreateDirectory "$DataRoot\backups"
  CreateDirectory "$DataRoot\exports"
  CreateDirectory "$DataRoot\temp"

  ${StrRep} $DataRootJson $DataRoot "\" "/"
  IfFileExists "$INSTDIR\storydriver.config.json" config_exists
  FileOpen $0 "$INSTDIR\storydriver.config.json" w
  FileWrite $0 "{$\r$\n"
  FileWrite $0 "  $\"dataRoot$\": $\"$DataRootJson$\",$\r$\n"
  FileWrite $0 "  $\"backendPort$\": 8001,$\r$\n"
  FileWrite $0 "  $\"lanEnabled$\": $LanEnabled,$\r$\n"
  FileWrite $0 "  $\"minimizeToTray$\": false$\r$\n"
  FileWrite $0 "}$\r$\n"
  FileClose $0
  config_exists:

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  ${If} $NoShortcuts != "true"
  CreateDirectory "$SMPROGRAMS\StoryDriver"
  CreateShortcut "$SMPROGRAMS\StoryDriver\StoryDriver.lnk" "$INSTDIR\StoryDriver.exe" "" "$INSTDIR\StoryDriver.exe" 0
  CreateShortcut "$SMPROGRAMS\StoryDriver\Uninstall StoryDriver.lnk" "$INSTDIR\Uninstall.exe"
  ${If} $DesktopShortcut == "true"
    CreateShortcut "$DESKTOP\StoryDriver.lnk" "$INSTDIR\StoryDriver.exe" "" "$INSTDIR\StoryDriver.exe" 0
  ${Else}
    Delete "$DESKTOP\StoryDriver.lnk"
  ${EndIf}
  ${EndIf}
  WriteRegStr HKCU "${PRODUCT_REGKEY}" "NoShortcuts" "$NoShortcuts"

  WriteRegStr HKCU "${PRODUCT_REGKEY}" "InstallRoot" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_REGKEY}" "DataRoot" "$DataRoot"
  WriteRegStr HKCU "${PRODUCT_REGKEY}" "LanEnabled" "$LanEnabled"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayName" "StoryDriver"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayIcon" "$INSTDIR\StoryDriver.exe"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "${UNINSTALL_REGKEY}" "NoModify" 1
  WriteRegDWORD HKCU "${UNINSTALL_REGKEY}" "NoRepair" 1
SectionEnd

Function un.onInit
  System::Call 'kernel32::OpenMutexW(i 0x100000, i 0, w "Local\StoryDriver.Desktop.Instance") p .r0'
  ${If} $0 != 0
    System::Call 'kernel32::CloseHandle(p r0)'
    MessageBox MB_ICONSTOP "Quit StoryDriver from its tray menu before uninstalling."
    Abort
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  ReadRegStr $NoShortcuts HKCU "${PRODUCT_REGKEY}" "NoShortcuts"
  ${If} $NoShortcuts != "true"
  Delete "$DESKTOP\StoryDriver.lnk"
  Delete "$SMPROGRAMS\StoryDriver\StoryDriver.lnk"
  Delete "$SMPROGRAMS\StoryDriver\Uninstall StoryDriver.lnk"
  RMDir "$SMPROGRAMS\StoryDriver"
  ${EndIf}

  DeleteRegKey HKCU "${UNINSTALL_REGKEY}"
  DeleteRegKey HKCU "${PRODUCT_REGKEY}"
  !include "${ROOT_DIR}\build\native\uninstall-files.nsh"
  Delete "$INSTDIR\storydriver.config.json"
  Delete "$INSTDIR\Uninstall.exe"
  RMDir "$INSTDIR"
SectionEnd
