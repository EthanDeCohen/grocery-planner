Attribute VB_Name = "GroceryPlannerModule"
Option Explicit

Public mNextScheduledRefresh As Date

Private Const HOURLY_REFRESH_PROC As String = "RefreshGroceryDataAuto"
Private Const AUTO_REFRESH_INTERVAL_HOURS As Long = 1

Public Sub RefreshGroceryData()
    RunRefreshGroceryData True
End Sub

Public Sub RefreshGroceryDataAuto()
    RunRefreshGroceryData False

    If AutoRefreshHourlyEnabled() Then
        ScheduleHourlyRefresh
    End If
End Sub

Public Sub ScheduleHourlyRefresh()
    CancelHourlyRefresh
    mNextScheduledRefresh = Now + TimeSerial(AUTO_REFRESH_INTERVAL_HOURS, 0, 0)
    Application.OnTime mNextScheduledRefresh, HOURLY_REFRESH_PROC
End Sub

Public Sub CancelHourlyRefresh()
    On Error Resume Next
    If mNextScheduledRefresh > 0 Then
        Application.OnTime EarliestTime:=mNextScheduledRefresh, _
            Procedure:=HOURLY_REFRESH_PROC, Schedule:=False
    End If
    mNextScheduledRefresh = 0
    On Error GoTo 0
End Sub

Public Function IsAutoSettingEnabled(ByVal settingValue As Variant) As Boolean
    Select Case UCase$(Trim$(CStr(settingValue)))
        Case "Y", "YES", "TRUE", "1"
            IsAutoSettingEnabled = True
        Case Else
            IsAutoSettingEnabled = False
    End Select
End Function

Private Function AutoRefreshHourlyEnabled() As Boolean
    AutoRefreshHourlyEnabled = IsAutoSettingEnabled( _
        ThisWorkbook.Worksheets("Instructions").Range("B8").Value)
End Function

Private Sub RunRefreshGroceryData(ByVal showMessages As Boolean)
    Dim previousScreenUpdating As Boolean
    Dim previousEvents As Boolean

    On Error GoTo CleanFail

    previousScreenUpdating = Application.ScreenUpdating
    previousEvents = Application.EnableEvents
    Application.ScreenUpdating = False
    Application.EnableEvents = False

    GroceryCsvImporter.RefreshAll ThisWorkbook

CleanExit:
    Application.ScreenUpdating = previousScreenUpdating
    Application.EnableEvents = previousEvents
    If showMessages Then
        MsgBox "Grocery data refreshed from CSV files.", vbInformation, "Grocery Planner"
    End If
    Exit Sub

CleanFail:
    Application.ScreenUpdating = previousScreenUpdating
    Application.EnableEvents = previousEvents
    If showMessages Then
        MsgBox "Refresh failed: " & Err.Description, vbCritical, "Grocery Planner"
    End If
End Sub

Public Sub OpenDataFolder()
    Dim dataRoot As String

    dataRoot = GroceryCsvImporter.ResolveDataRoot(ThisWorkbook)

    If dataRoot = "" Then
        MsgBox "Data folder not found. Save this workbook beside the data folder first.", _
            vbExclamation, "Grocery Planner"
        Exit Sub
    End If

    Shell "explorer.exe """ & dataRoot & """", vbNormalFocus
End Sub