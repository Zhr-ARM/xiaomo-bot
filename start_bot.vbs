' 小源 QQ 机器人 - 静默启动（无控制台窗口）
Set ws = CreateObject("Wscript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
batPath = fso.BuildPath(scriptDir, "start_bot.bat")
ws.CurrentDirectory = scriptDir
ws.Run "cmd /c " & Chr(34) & batPath & Chr(34), 0, False
