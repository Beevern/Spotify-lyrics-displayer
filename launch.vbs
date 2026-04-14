Dim fso, dir, cmd
Set fso = CreateObject("Scripting.FileSystemObject")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
cmd = "pythonw """ & dir & "\main.py"""
CreateObject("WScript.Shell").Run cmd, 0, False
