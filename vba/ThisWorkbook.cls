VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "ThisWorkbook"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Option Explicit

Private Sub Workbook_Open()
    On Error GoTo CleanFail

    If AutoRefreshOnOpenEnabled() Then
        RefreshGroceryDataAuto
    ElseIf AutoRefreshHourlyEnabled() Then
        ScheduleHourlyRefresh
    End If
    Exit Sub

CleanFail:
    ' Do not block opening the workbook if auto-refresh fails.
End Sub

Private Sub Workbook_BeforeClose(Cancel As Boolean)
    CancelHourlyRefresh
End Sub

Private Function AutoRefreshOnOpenEnabled() As Boolean
    AutoRefreshOnOpenEnabled = IsAutoSettingEnabled( _
        ThisWorkbook.Worksheets("Instructions").Range("B7").Value)
End Function

Private Function AutoRefreshHourlyEnabled() As Boolean
    AutoRefreshHourlyEnabled = IsAutoSettingEnabled( _
        ThisWorkbook.Worksheets("Instructions").Range("B8").Value)
End Function