; kicad-ja-plus の Windows インストーラー（makensis -DVERSION= -DSTAGE= -DOUTFILE=）
Unicode true
!include "MUI2.nsh"

Name "KiCad (kicad-ja-plus) ${VERSION}"
OutFile "${OUTFILE}"
InstallDir "$PROGRAMFILES64\KiCad-ja-plus\${VERSION}"
RequestExecutionLevel admin
SetCompressor /SOLID lzma

!define REGKEY "Software\Microsoft\Windows\CurrentVersion\Uninstall\KiCad-ja-plus-${VERSION}"

!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "Japanese"
!insertmacro MUI_LANGUAGE "English"

Section "Install"
  SetOutPath "$INSTDIR"
  File /r "${STAGE}\*.*"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateDirectory "$SMPROGRAMS\KiCad-ja-plus ${VERSION}"
  CreateShortcut "$SMPROGRAMS\KiCad-ja-plus ${VERSION}\KiCad.lnk" "$INSTDIR\bin\kicad.exe"
!if /FileExists "${STAGE}\freerouting\freerouting.jar"
  ; 同梱の Freerouting（全体の自動配線）を、同梱の Java で（画面あり）
  CreateShortcut "$SMPROGRAMS\KiCad-ja-plus ${VERSION}\Freerouting.lnk" "$INSTDIR\freerouting\jre\bin\javaw.exe" '-jar "$INSTDIR\freerouting\freerouting.jar"'
!endif
  CreateShortcut "$SMPROGRAMS\KiCad-ja-plus ${VERSION}\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKLM "${REGKEY}" "DisplayName" "KiCad (kicad-ja-plus) ${VERSION}"
  WriteRegStr HKLM "${REGKEY}" "DisplayVersion" "${VERSION}"
  WriteRegStr HKLM "${REGKEY}" "Publisher" "kicad-ja-plus"
  WriteRegStr HKLM "${REGKEY}" "InstallLocation" "$INSTDIR"
  WriteRegStr HKLM "${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  WriteRegStr HKLM "${REGKEY}" "QuietUninstallString" '"$INSTDIR\Uninstall.exe" /S'
  WriteRegDWORD HKLM "${REGKEY}" "NoModify" 1
  WriteRegDWORD HKLM "${REGKEY}" "NoRepair" 1
SectionEnd

Section "Uninstall"
  RMDir /r "$SMPROGRAMS\KiCad-ja-plus ${VERSION}"
  DeleteRegKey HKLM "${REGKEY}"
  RMDir /r "$INSTDIR"
SectionEnd
