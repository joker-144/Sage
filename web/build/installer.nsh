; Sage NSIS 自定义脚本
; 确保安装路径末尾始终包含 "Sage" 子文件夹

Function .onVerifyInstDir
  Push $0
  Push $1
  StrCpy $0 $INSTDIR
  StrCpy $1 $0 "" -4
  StrCmp $1 "Sage" skip append
  append:
    StrCpy $INSTDIR "$0\Sage"
  skip:
  Pop $1
  Pop $0
FunctionEnd
