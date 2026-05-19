' 小源 QQ 机器人 - 静默启动（无控制台窗口）
Set ws = CreateObject("Wscript.Shell")
ws.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
ws.Run "cmd /c start_bot.bat", 0, False
