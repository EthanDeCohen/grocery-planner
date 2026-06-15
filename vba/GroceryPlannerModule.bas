Attribute VB_Name = "GroceryPlannerModule"
Option Explicit

Public Sub RefreshGroceryData()
    Dim importer As GroceryCsvImporter
    Dim previousScreenUpdating As Boolean
    Dim previousEvents As Boolean

    On Error GoTo CleanFail

    previousScreenUpdating = Application.ScreenUpdating
    previousEvents = Application.EnableEvents
    Application.ScreenUpdating = False
    Application.EnableEvents = False

    Set importer = New GroceryCsvImporter
    importer.Init ThisWorkbook
    importer.RefreshAll

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
    Dim importer As GroceryCsvImporter
    Dim dataRoot As String

    Set importer = New GroceryCsvImporter
    importer.Init ThisWorkbook
    dataRoot = importer.DataRoot

    If dataRoot = "" Then
        MsgBox "Data folder not found. Save this workbook beside the data folder first.", _
            vbExclamation, "Grocery Planner"
        Exit Sub
    End If

    Shell "explorer.exe """ & dataRoot & """", vbNormalFocus
End Sub