Attribute VB_Name = "GroceryPlannerModule"
Option Explicit

Public Sub RefreshGroceryData()
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
    MsgBox "Grocery data refreshed from CSV files.", vbInformation, "Grocery Planner"
    Exit Sub

CleanFail:
    Application.ScreenUpdating = previousScreenUpdating
    Application.EnableEvents = previousEvents
    MsgBox "Refresh failed: " & Err.Description, vbCritical, "Grocery Planner"
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