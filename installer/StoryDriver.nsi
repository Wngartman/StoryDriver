Unicode True
ManifestDPIAware true
RequestExecutionLevel user
SetCompressor /SOLID lzma

!include "MUI2.nsh"
!include "FileFunc.nsh"
!include "LogicLib.nsh"
!include "nsDialogs.nsh"
!include "StrFunc.nsh"
${StrRep}

!ifndef ROOT_DIR
!define ROOT_DIR "${__FILEDIR__}\.."
!endif
!define PRODUCT_NAME "StoryDriver"
!define PRODUCT_VERSION "1.0.0-rc.1"
!define PRODUCT_PUBLISHER "StoryDriver"
!define PRODUCT_REGKEY "Software\StoryDriver"
!define UNINSTALL_REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\StoryDriver"
!define PAYLOAD_DIR "${ROOT_DIR}\build\native\app"

Name "${PRODUCT_NAME} ${PRODUCT_VERSION}"
OutFile "${ROOT_DIR}\release\StoryDriver-Setup-x64.exe"
InstallDir "D:\StoryDriverApp"
InstallDirRegKey HKCU "${PRODUCT_REGKEY}" "InstallRoot"
Icon "${ROOT_DIR}\assets\desktop\storydriver.ico"
UninstallIcon "${ROOT_DIR}\assets\desktop\storydriver.ico"
BrandingText "StoryDriver - local and private"

Var DataRoot
Var DataRootJson
Var DataRootField
Var LanEnabled
Var LanCheckbox
Var DesktopShortcut
Var DesktopCheckbox
Var RemoveData

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
  StrCpy $DataRoot "D:\StoryDriverData"
  StrCpy $LanEnabled "false"
  StrCpy $DesktopShortcut "false"

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
FunctionEnd

Function DataOptionsCreate
  nsDialogs::Create 1018
  Pop $0
  ${If} $0 == error
    Abort
  ${EndIf}

  !insertmacro MUI_HEADER_TEXT "Local data" "Choose where StoryDriver stores writing and generated media."
  ${NSD_CreateLabel} 0 0 100% 26u "This folder is separate from the application so upgrades and uninstall can preserve it."
  Pop $0
  ${NSD_CreateText} 0 32u 100% 13u "$DataRoot"
  Pop $DataRootField
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
  SetOutPath "$INSTDIR"
  File /r "${PAYLOAD_DIR}\*.*"

  CreateDirectory "$DataRoot"
  CreateDirectory "$DataRoot\logs"
  CreateDirectory "$DataRoot\backups"
  CreateDirectory "$DataRoot\exports"
  CreateDirectory "$DataRoot\temp"

  ${StrRep} $DataRootJson $DataRoot "\" "/"
  FileOpen $0 "$INSTDIR\storydriver.config.json" w
  FileWrite $0 "{$\r$\n"
  FileWrite $0 "  $\"dataRoot$\": $\"$DataRootJson$\",$\r$\n"
  FileWrite $0 "  $\"backendPort$\": 8001,$\r$\n"
  FileWrite $0 "  $\"lanEnabled$\": $LanEnabled,$\r$\n"
  FileWrite $0 "  $\"minimizeToTray$\": false$\r$\n"
  FileWrite $0 "}$\r$\n"
  FileClose $0

  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\StoryDriver"
  CreateShortcut "$SMPROGRAMS\StoryDriver\StoryDriver.lnk" "$INSTDIR\StoryDriver.exe" "" "$INSTDIR\StoryDriver.exe" 0
  CreateShortcut "$SMPROGRAMS\StoryDriver\Uninstall StoryDriver.lnk" "$INSTDIR\Uninstall.exe"
  ${If} $DesktopShortcut == "true"
    CreateShortcut "$DESKTOP\StoryDriver.lnk" "$INSTDIR\StoryDriver.exe" "" "$INSTDIR\StoryDriver.exe" 0
  ${Else}
    Delete "$DESKTOP\StoryDriver.lnk"
  ${EndIf}

  WriteRegStr HKCU "${PRODUCT_REGKEY}" "InstallRoot" "$INSTDIR"
  WriteRegStr HKCU "${PRODUCT_REGKEY}" "DataRoot" "$DataRoot"
  WriteRegStr HKCU "${PRODUCT_REGKEY}" "LanEnabled" "$LanEnabled"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayName" "StoryDriver"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayVersion" "${PRODUCT_VERSION}"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "Publisher" "${PRODUCT_PUBLISHER}"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "DisplayIcon" "$INSTDIR\StoryDriver.exe"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKCU "${UNINSTALL_REGKEY}" "UninstallString" '$\"$INSTDIR\Uninstall.exe$\"'
  WriteRegDWORD HKCU "${UNINSTALL_REGKEY}" "NoModify" 0
  WriteRegDWORD HKCU "${UNINSTALL_REGKEY}" "NoRepair" 0
SectionEnd

Function un.onInit
  StrCpy $RemoveData "false"
  ReadRegStr $DataRoot HKCU "${PRODUCT_REGKEY}" "DataRoot"
  ${GetParameters} $0
  ${GetOptions} $0 "/REMOVEDATA=" $1
  ${If} $1 == "1"
    StrCpy $RemoveData "true"
  ${ElseIfNot} ${Silent}
    MessageBox MB_ICONQUESTION|MB_YESNO|MB_DEFBUTTON2 "Remove StoryDriver user data from:$\r$\n$DataRoot$\r$\n$\r$\nChoose No to preserve stories, voices, settings, and generated narration." IDNO +2
    StrCpy $RemoveData "true"
  ${EndIf}
FunctionEnd

Section "Uninstall"
  SetShellVarContext current
  Delete "$DESKTOP\StoryDriver.lnk"
  RMDir /r "$SMPROGRAMS\StoryDriver"

  ${If} $RemoveData == "true"
    StrLen $0 $DataRoot
    ${If} $0 > 3
      RMDir /r "$DataRoot"
    ${EndIf}
  ${EndIf}

  DeleteRegKey HKCU "${UNINSTALL_REGKEY}"
  DeleteRegKey HKCU "${PRODUCT_REGKEY}"
  RMDir /r "$INSTDIR"
SectionEnd
