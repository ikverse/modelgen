' Starts ModelGen with no cmd window: runs ModelGen.bat hidden. The Start menu and desktop icons use this.
' ModelGen stops by itself a little after its last page closes. Pass /noopen to skip opening the page.
Set shell = CreateObject("WScript.Shell")
folder = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
args = ""
For Each a In WScript.Arguments
    args = args & " " & a
Next
shell.CurrentDirectory = folder
shell.Run "cmd /c """"" & folder & "\ModelGen.bat"" /hidden" & args & """", 0, False
