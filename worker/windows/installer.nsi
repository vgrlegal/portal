; VGR Legal / Opinion & Ownership TP Robot - one installer source, two brands.
; Build: makensis installer.nsi                          -> VGR-Legal-TP-Robot-Setup.exe
;        makensis -DOO installer.nsi                     -> Opinion-and-Ownership-TP-Robot-Setup.exe
Unicode true
!ifdef OO
  !define NAME "Opinion and Ownership TP Robot"
  !define COMPANY "Opinion and Ownership"
  !define ORG "oo"
  !define ICO "oo.ico"
  !define KEYDIR "OpinionOwnership"
  !define REGKEY "OOTPRobot"
  OutFile "Opinion-and-Ownership-TP-Robot-Setup.exe"
!else
  !define NAME "VGR Legal TP Robot"
  !define COMPANY "VGR Legal"
  !define ORG "vgr"
  !define ICO "vgr.ico"
  !define KEYDIR "VGRLegal"
  !define REGKEY "VGRLegalTPRobot"
  OutFile "VGR-Legal-TP-Robot-Setup.exe"
!endif
Name "${NAME}"
Caption "${NAME} Setup"
Icon "${ICO}"
UninstallIcon "${ICO}"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\${KEYDIR}\TPRobot"
VIProductVersion "1.1.0.0"
VIAddVersionKey "ProductName" "${NAME}"
VIAddVersionKey "CompanyName" "${COMPANY}"
VIAddVersionKey "FileDescription" "${NAME} Setup"
VIAddVersionKey "FileVersion" "1.1"
VIAddVersionKey "LegalCopyright" "${COMPANY}"
BrandingText "${COMPANY}"
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File "setup.ps1"
  File "brand.ps1"
  File "status.ps1"
  File "uninstall.ps1"
  File /oname=robot.ico "${ICO}"
  ; brand.txt: the name on every window, the organisation whose TPs this laptop takes
  FileOpen $0 "$INSTDIR\brand.txt" w
  FileWrite $0 "NAME=${NAME}$\r$\nORG=${ORG}$\r$\n"
  FileClose $0
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\${NAME}.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\status.ps1"' "$INSTDIR\robot.ico"
  CreateDirectory "$SMPROGRAMS\${COMPANY}"
  CreateShortCut "$SMPROGRAMS\${COMPANY}\${NAME}.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\status.ps1"' "$INSTDIR\robot.ico"
  CreateShortCut "$SMPROGRAMS\${COMPANY}\Set up again.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"' "$INSTDIR\robot.ico"
  CreateShortCut "$SMPROGRAMS\${COMPANY}\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "DisplayName" "${NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "Publisher" "${COMPANY}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "DisplayIcon" "$INSTDIR\robot.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  ; opens a window that installs Python, the robot and its Chrome, then asks for the key
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"'
SectionEnd

Section "Uninstall"
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\uninstall.ps1"'
  Delete "$DESKTOP\${NAME}.lnk"
  RMDir /r "$SMPROGRAMS\${COMPANY}"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\${REGKEY}"
  RMDir /r "$INSTDIR"
SectionEnd
