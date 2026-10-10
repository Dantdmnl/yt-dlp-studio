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

    command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & installScript & """ -Interactive -Shortcuts"
    On Error Resume Next
    exitCode = shell.Run(command, 1, True)
    If Err.Number <> 0 Then
        MsgBox "Could not open setup." & vbCrLf & vbCrLf & Err.Description & vbCrLf & _
            "Try double-clicking Setup.cmd in this folder.", vbCritical, "yt-dlp Studio setup"
        WScript.Quit 1
    End If
    On Error GoTo 0
    If exitCode <> 0 Or Not fso.FileExists(venvPython) Then
        MsgBox "Setup did not finish successfully." & vbCrLf & vbCrLf & _
            "Double-click Setup.cmd in this folder to try again." & vbCrLf & _
            "See docs\troubleshooting.md for repair steps.", vbCritical, "yt-dlp Studio setup"
        WScript.Quit 1
    End If
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & runScript & """"
On Error Resume Next
exitCode = shell.Run(command, 0, True)
If Err.Number <> 0 Then
    MsgBox "Could not start the PowerShell launcher." & vbCrLf & vbCrLf & _
        Err.Description, vbCritical, "yt-dlp Studio"
    WScript.Quit 1
End If
On Error GoTo 0
WScript.Quit exitCode
