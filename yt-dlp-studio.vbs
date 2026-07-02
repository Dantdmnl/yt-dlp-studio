Option Explicit

Dim shell, fso, appDir, runScript, installScript, venvPython, command, answer, exitCode

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
runScript = fso.BuildPath(appDir, "run-gui.ps1")
installScript = fso.BuildPath(appDir, "install.ps1")
venvPython = fso.BuildPath(appDir, ".venv\Scripts\python.exe")

If Not fso.FileExists(runScript) Then
    MsgBox "yt-dlp Studio could not start because run-gui.ps1 was not found." & vbCrLf & vbCrLf & _
        "Expected path:" & vbCrLf & runScript, vbCritical, "yt-dlp Studio"
    WScript.Quit 1
End If

If Not fso.FileExists(venvPython) Then
    If Not fso.FileExists(installScript) Then
        MsgBox "yt-dlp Studio is not installed yet, and install.ps1 was not found." & vbCrLf & vbCrLf & _
            "Expected path:" & vbCrLf & installScript, vbCritical, "yt-dlp Studio"
        WScript.Quit 1
    End If

    answer = MsgBox( _
        "yt-dlp Studio needs to finish first-time setup before it can start." & vbCrLf & vbCrLf & _
        "This will open a PowerShell setup window, create .venv, and install the required packages." & vbCrLf & vbCrLf & _
        "Continue?", _
        vbQuestion + vbYesNo, _
        "yt-dlp Studio setup" _
    )

    If answer <> vbYes Then
        WScript.Quit 0
    End If

    command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & installScript & """ -NoPause"
    exitCode = shell.Run(command, 1, True)
    If exitCode <> 0 Or Not fso.FileExists(venvPython) Then
        MsgBox "Setup did not finish successfully." & vbCrLf & vbCrLf & _
            "Open PowerShell in this folder and run:" & vbCrLf & _
            ".\install.ps1 -Recreate", vbCritical, "yt-dlp Studio setup"
        WScript.Quit 1
    End If
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & runScript & """"
exitCode = shell.Run(command, 0, False)
If exitCode <> 0 Then
    MsgBox "yt-dlp Studio could not be launched." & vbCrLf & vbCrLf & _
        "Try running .\run-gui.ps1 -Foreground from PowerShell for more details.", vbCritical, "yt-dlp Studio"
    WScript.Quit exitCode
End If
