Unicode true
!define NAME "VGR Legal TP Robot"
Name "${NAME}"
OutFile "VGR-Legal-TP-Robot-Setup.exe"
Icon "vgr.ico"
UninstallIcon "vgr.ico"
RequestExecutionLevel user
InstallDir "$LOCALAPPDATA\VGRLegal\TPRobot"
VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "${NAME}"
VIAddVersionKey "CompanyName" "VGR Legal"
VIAddVersionKey "FileDescription" "${NAME} Setup"
VIAddVersionKey "FileVersion" "1.0"
VIAddVersionKey "LegalCopyright" "VGR Legal"
BrandingText "VGR Legal"
Page directory
Page instfiles
UninstPage uninstConfirm
UninstPage instfiles

Section "Install"
  SetOutPath "$INSTDIR"
  File "setup.ps1"
  File "status.ps1"
  File "uninstall.ps1"
  File "vgr.ico"
  WriteUninstaller "$INSTDIR\Uninstall.exe"
  CreateShortCut "$DESKTOP\${NAME}.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\status.ps1"' "$INSTDIR\vgr.ico"
  CreateDirectory "$SMPROGRAMS\VGR Legal"
  CreateShortCut "$SMPROGRAMS\VGR Legal\${NAME}.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\status.ps1"' "$INSTDIR\vgr.ico"
  CreateShortCut "$SMPROGRAMS\VGR Legal\Set up again.lnk" "powershell.exe" '-NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"' "$INSTDIR\vgr.ico"
  CreateShortCut "$SMPROGRAMS\VGR Legal\Uninstall.lnk" "$INSTDIR\Uninstall.exe"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VGRLegalTPRobot" "DisplayName" "${NAME}"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VGRLegalTPRobot" "Publisher" "VGR Legal"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VGRLegalTPRobot" "DisplayIcon" "$INSTDIR\vgr.ico"
  WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VGRLegalTPRobot" "UninstallString" '"$INSTDIR\Uninstall.exe"'
  ; opens a window that installs Python, the robot and its Chrome, then asks for the key
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\setup.ps1"'
SectionEnd

Section "Uninstall"
  ExecWait 'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$INSTDIR\uninstall.ps1"'
  Delete "$DESKTOP\${NAME}.lnk"
  RMDir /r "$SMPROGRAMS\VGR Legal"
  DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\VGRLegalTPRobot"
  RMDir /r "$INSTDIR"
SectionEnd
