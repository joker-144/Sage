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

; ── 卸载时询问是否删除本地用户数据 ──────────────────────
; 默认选"是"（MB_YESNO 默认按钮为 YES）
; 删除范围：%LOCALAPPDATA%/Sage（.env 配置、memory.db 对话记录、settings.json、技能）
;          %APPDATA%/sage/workspaces/（工作空间、论文 PDF、索引数据库）
!macro customUnInstall
  Push $0
  MessageBox MB_YESNO|MB_ICONQUESTION "是否同时删除本地数据？$\n$\n将删除：对话记录、模型配置、工作空间论文、索引数据库等全部用户数据。" IDYES delete_user_data IDNO skip_delete_user_data
  delete_user_data:
    ReadEnvStr $0 "LOCALAPPDATA"
    RMDir /r "$0\Sage"
    ReadEnvStr $0 "APPDATA"
    RMDir /r "$0\sage"
    Goto done_delete_user_data
  skip_delete_user_data:
  done_delete_user_data:
  Pop $0
!macroend
