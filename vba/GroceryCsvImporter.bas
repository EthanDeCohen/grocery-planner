Attribute VB_Name = "GroceryCsvImporter"
Option Explicit

Private Const DATA_FOLDER_NAME As String = "data"
Private Const PRICES_COMBINED_SHEET As String = "All Prices"
Private Const DEALS_COMBINED_SHEET As String = "All Deals"

Public Function ResolveDataRoot(ByVal wb As Workbook) As String
    Dim fso As Object
    Dim searchPaths As Collection
    Dim searchPath As Variant
    Dim parentPath As String
    Dim candidate As String
    Dim overridePath As String

    Set fso = CreateObject("Scripting.FileSystemObject")
    overridePath = InstructionsDataOverride(wb)
    If overridePath <> "" Then
        If fso.FolderExists(overridePath) Then
            ResolveDataRoot = overridePath
            Exit Function
        End If
    End If

    Set searchPaths = BuildWorkbookSearchPaths(wb, fso)

    For Each searchPath In searchPaths
        Do
            candidate = fso.BuildPath(CStr(searchPath), DATA_FOLDER_NAME)
            If DataFolderExists(fso, candidate) Then
                ResolveDataRoot = candidate
                Exit Function
            End If

            parentPath = fso.GetParentFolderName(CStr(searchPath))
            If parentPath = CStr(searchPath) Then Exit Do
            searchPath = parentPath
        Loop
    Next searchPath

    ResolveDataRoot = FindKnownProjectDataFolder(fso)
End Function

Private Function InstructionsDataOverride(ByVal wb As Workbook) As String
    On Error Resume Next
    InstructionsDataOverride = Trim$(CStr(wb.Worksheets("Instructions").Range("B6").Value))
    On Error GoTo 0
End Function

Private Function BuildWorkbookSearchPaths(ByVal wb As Workbook, ByVal fso As Object) As Collection
    Dim paths As New Collection

    AddMappedSearchPaths paths, wb.Path, fso
    If Len(wb.FullName) > 0 Then
        AddMappedSearchPaths paths, fso.GetParentFolderName(wb.FullName), fso
    End If

    Set BuildWorkbookSearchPaths = paths
End Function

Private Sub AddMappedSearchPaths(ByVal paths As Collection, ByVal workbookPath As String, ByVal fso As Object)
    Dim relPath As String
    Dim oneDriveRoot As Variant

    If Len(workbookPath) = 0 Then Exit Sub

    If LCase$(Left$(workbookPath, 4)) <> "http" Then
        AddUniquePath paths, workbookPath
        Exit Sub
    End If

    relPath = OneDriveRelativePathFromUrl(workbookPath)
    If relPath = "" Then Exit Sub

    For Each oneDriveRoot In GetOneDriveRoots(fso)
        AddUniquePath paths, fso.BuildPath(CStr(oneDriveRoot), relPath)
    Next oneDriveRoot
End Sub

Private Function FindKnownProjectDataFolder(ByVal fso As Object) As String
    Dim oneDriveRoot As Variant
    Dim projectFolder As Variant
    Dim candidate As String

    For Each oneDriveRoot In GetOneDriveRoots(fso)
        For Each projectFolder In Array("Desktop\groceryPlanner", "Documents\groceryPlanner")
            candidate = fso.BuildPath(CStr(oneDriveRoot), CStr(projectFolder))
            candidate = fso.BuildPath(candidate, DATA_FOLDER_NAME)
            If DataFolderExists(fso, candidate) Then
                FindKnownProjectDataFolder = candidate
                Exit Function
            End If
        Next projectFolder
    Next oneDriveRoot

    FindKnownProjectDataFolder = ""
End Function

Private Function GetOneDriveRoots(ByVal fso As Object) As Collection
    Dim roots As New Collection
    Dim profileFolder As Object
    Dim childFolder As Object

    On Error Resume Next
    Set profileFolder = fso.GetFolder(Environ$("USERPROFILE"))
    On Error GoTo 0

    If Not profileFolder Is Nothing Then
        For Each childFolder In profileFolder.SubFolders
            If LCase$(Left$(childFolder.Name, 8)) = "onedrive" Then
                AddUniquePath roots, childFolder.Path
            End If
        Next childFolder
    End If

    AddUniquePath roots, Environ$("USERPROFILE") & "\OneDrive"
    AddUniquePath roots, Environ$("USERPROFILE") & "\OneDrive - Personal"
    Set GetOneDriveRoots = roots
End Function

Private Function OneDriveRelativePathFromUrl(ByVal urlPath As String) As String
    Dim normalized As String
    Dim markers As Variant
    Dim marker As Variant
    Dim pos As Long
    Dim parts() As String
    Dim i As Long

    normalized = Replace$(Replace$(urlPath, "\", "/"), "https://", vbNullString)
    normalized = Replace$(normalized, "http://", vbNullString)

    markers = Array("/Desktop/", "/Documents/", "/desktop/", "/documents/")
    For Each marker In markers
        pos = InStr(1, normalized, CStr(marker), vbTextCompare)
        If pos > 0 Then
            OneDriveRelativePathFromUrl = Replace$(Mid$(normalized, pos + Len(marker)), "/", "\")
            If Right$(OneDriveRelativePathFromUrl, 1) = "\" Then
                OneDriveRelativePathFromUrl = Left$(OneDriveRelativePathFromUrl, Len(OneDriveRelativePathFromUrl) - 1)
            End If
            Exit Function
        End If
    Next marker

    parts = Split(normalized, "/")
    If UBound(parts) < 2 Then Exit Function

    OneDriveRelativePathFromUrl = parts(2)
    For i = 3 To UBound(parts)
        If Len(parts(i)) > 0 Then
            OneDriveRelativePathFromUrl = OneDriveRelativePathFromUrl & "\" & parts(i)
        End If
    Next i
End Function

Private Function DataFolderExists(ByVal fso As Object, ByVal folderPath As String) As Boolean
    On Error Resume Next
    DataFolderExists = fso.FolderExists(folderPath)
    If Not DataFolderExists Then
        DataFolderExists = Len(Dir(folderPath, vbDirectory)) > 0
    End If
    On Error GoTo 0
End Function

Private Function CollectionContainsPath(ByVal paths As Collection, ByVal pathValue As String) As Boolean
    Dim item As Variant

    For Each item In paths
        If StrComp(CStr(item), pathValue, vbTextCompare) = 0 Then
            CollectionContainsPath = True
            Exit Function
        End If
    Next item
End Function

Private Sub AddUniquePath(ByVal paths As Collection, ByVal pathValue As String)
    If Len(pathValue) > 0 Then
        If Not CollectionContainsPath(paths, pathValue) Then paths.Add pathValue
    End If
End Sub

Public Sub RefreshAll(ByVal targetWorkbook As Workbook)
    Dim dataRoot As String
    Dim storeName As String
    Dim folderName As String
    Dim pricesSheet As String
    Dim dealsSheet As String
    Dim storeIndex As Long
    Dim pricesCombined As Worksheet
    Dim dealsCombined As Worksheet
    Dim nextPricesRow As Long
    Dim nextDealsRow As Long
    Dim importedPrices As Long
    Dim importedDeals As Long
    Dim totalPrices As Long
    Dim totalDeals As Long
    Dim summary As String

    dataRoot = ResolveDataRoot(targetWorkbook)
    If dataRoot = "" Then
        Err.Raise vbObjectError + 513, "GroceryCsvImporter", _
            "Could not locate the data folder. Save GroceryPlanner.xlsm in the " & _
            "project root beside '" & DATA_FOLDER_NAME & "', or set the full path " & _
            "to your data folder in Instructions!B6."
    End If

    Set pricesCombined = EnsureSheet(targetWorkbook, PRICES_COMBINED_SHEET)
    Set dealsCombined = EnsureSheet(targetWorkbook, DEALS_COMBINED_SHEET)

    ClearSheetData pricesCombined, 1
    ClearSheetData dealsCombined, 1

    nextPricesRow = 2
    nextDealsRow = 2

    For storeIndex = 1 To STORE_COUNT
        GetStoreConfig storeIndex, storeName, folderName, pricesSheet, dealsSheet

        importedPrices = ImportStoreCsv(targetWorkbook, _
            PricesCsvPath(dataRoot, folderName), pricesSheet, pricesCombined, _
            nextPricesRow, storeName, "price")
        importedDeals = ImportStoreCsv(targetWorkbook, _
            DealsCsvPath(dataRoot, folderName), dealsSheet, dealsCombined, _
            nextDealsRow, storeName, "deal")

        totalPrices = totalPrices + importedPrices
        totalDeals = totalDeals + importedDeals
    Next storeIndex

    ApplyTableFormatting pricesCombined, nextPricesRow - 1
    ApplyTableFormatting dealsCombined, nextDealsRow - 1
    UpdateSavingsSummary targetWorkbook, dataRoot, totalPrices, totalDeals

    summary = "Imported " & totalPrices & " price row(s) and " & totalDeals & _
        " deal row(s) from " & dataRoot
    targetWorkbook.Worksheets("Instructions").Range("B4").Value = Now
    targetWorkbook.Worksheets("Instructions").Range("B5").Value = summary
End Sub

Private Function ImportStoreCsv(ByVal targetWorkbook As Workbook, ByVal csvPath As String, _
    ByVal sheetName As String, ByVal combinedSheet As Worksheet, ByRef nextCombinedRow As Long, _
    ByVal storeLabel As String, ByVal rowType As String) As Long

    Dim ws As Worksheet
    Dim rowCount As Long

    Set ws = EnsureSheet(targetWorkbook, sheetName)
    ClearSheetData ws, 1

    If Not CsvFileExists(csvPath) Then
        ws.Range("A2").Value = "No CSV found at " & csvPath
        ImportStoreCsv = 0
        Exit Function
    End If

    rowCount = LoadCsvToSheet(csvPath, ws, storeLabel, rowType)
    If rowCount > 0 Then
        AppendSheetRows ws, combinedSheet, nextCombinedRow, rowCount
        nextCombinedRow = nextCombinedRow + rowCount
    End If

    ApplyTableFormatting ws, rowCount + 1
    ImportStoreCsv = rowCount
End Function

Private Function LoadCsvToSheet(ByVal csvPath As String, ByVal ws As Worksheet, _
    ByVal storeLabel As String, ByVal rowType As String) As Long

    Dim queryTable As QueryTable
    Dim queryPath As String
    Dim lastRow As Long
    Dim dataRows As Long

    ws.Range("A1").CurrentRegion.Clear
    ws.Range("A1").Value = "store"
    ws.Range("B1").Value = "row_type"

    queryPath = MaterializeCsvForExcel(csvPath)

    Set queryTable = ws.QueryTables.Add( _
        Connection:="TEXT;" & queryPath, _
        Destination:=ws.Range("C1"))

    On Error GoTo CleanFail
    With queryTable
        .TextFileParseType = xlDelimited
        .TextFileCommaDelimiter = True
        .TextFileConsecutiveDelimiter = False
        .TextFileTrailingMinusNumbers = True
        .Refresh BackgroundQuery:=False
        .Delete
    End With
    On Error GoTo 0

    lastRow = ws.Cells(ws.Rows.Count, "C").End(xlUp).Row
    If lastRow < 2 Then
        LoadCsvToSheet = 0
        Exit Function
    End If

    dataRows = lastRow - 1

    ws.Columns("A:B").Insert Shift:=xlToRight
    ws.Range("A1").Value = "store"
    ws.Range("B1").Value = "row_type"
    ws.Range("A2:A" & lastRow).Value = storeLabel
    ws.Range("B2:B" & lastRow).Value = rowType

    LoadCsvToSheet = dataRows
    Exit Function

CleanFail:
    On Error Resume Next
    queryTable.Delete
    On Error GoTo 0
    Err.Raise Err.Number, "GroceryCsvImporter", "Failed to import CSV: " & csvPath & " (" & Err.Description & ")"
End Function

Private Function MaterializeCsvForExcel(ByVal csvPath As String) As String
    Dim fso As Object
    Dim tempPath As String

    Set fso = CreateObject("Scripting.FileSystemObject")
    tempPath = fso.BuildPath(Environ$("TEMP"), "groceryplanner_" & fso.GetFileName(csvPath))

    If fso.FileExists(tempPath) Then fso.DeleteFile tempPath, True
    fso.CopyFile csvPath, tempPath, True
    MaterializeCsvForExcel = tempPath
End Function

Private Sub AppendSheetRows(ByVal sourceSheet As Worksheet, ByVal targetSheet As Worksheet, _
    ByVal targetStartRow As Long, ByVal rowCount As Long)

    Dim sourceRange As Range
    Dim lastCol As Long

    lastCol = sourceSheet.Cells(1, sourceSheet.Columns.Count).End(xlToLeft).Column
    Set sourceRange = sourceSheet.Range(sourceSheet.Cells(2, 1), sourceSheet.Cells(rowCount + 1, lastCol))

    If targetStartRow = 2 And targetSheet.Range("A1").Value = "" Then
        sourceSheet.Rows(1).Copy Destination:=targetSheet.Rows(1)
    End If

    sourceRange.Copy Destination:=targetSheet.Cells(targetStartRow, 1)
End Sub

Private Function CsvFileExists(ByVal csvPath As String) As Boolean
    Dim fso As Object
    Set fso = CreateObject("Scripting.FileSystemObject")
    CsvFileExists = fso.FileExists(csvPath)
End Function

Private Function EnsureSheet(ByVal targetWorkbook As Workbook, ByVal sheetName As String) As Worksheet
    On Error Resume Next
    Set EnsureSheet = targetWorkbook.Worksheets(sheetName)
    On Error GoTo 0

    If EnsureSheet Is Nothing Then
        Set EnsureSheet = targetWorkbook.Worksheets.Add(After:=targetWorkbook.Worksheets(targetWorkbook.Worksheets.Count))
        EnsureSheet.Name = sheetName
    End If
End Function

Private Sub ClearSheetData(ByVal ws As Worksheet, ByVal keepHeaderRows As Long)
    Dim lastRow As Long
    Dim lastCol As Long

    lastRow = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column

    If lastRow > keepHeaderRows Then
        ws.Range(ws.Cells(keepHeaderRows + 1, 1), ws.Cells(lastRow, lastCol)).ClearContents
    End If
End Sub

Private Sub ApplyTableFormatting(ByVal ws As Worksheet, ByVal lastRow As Long)
    Dim lastCol As Long
    Dim headerRange As Range

    lastCol = ws.Cells(1, ws.Columns.Count).End(xlToLeft).Column
    If lastCol < 1 Or lastRow < 1 Then Exit Sub

    Set headerRange = ws.Range(ws.Cells(1, 1), ws.Cells(1, lastCol))
    With headerRange
        .Font.Bold = True
        .Interior.Color = RGB(230, 240, 250)
    End With

    ws.Columns.AutoFit
End Sub

Private Sub UpdateSavingsSummary(ByVal targetWorkbook As Workbook, ByVal dataRoot As String, _
    ByVal totalPrices As Long, ByVal totalDeals As Long)

    Dim ws As Worksheet
    Set ws = EnsureSheet(targetWorkbook, "Savings Summary")

    ws.Range("A1").Value = "Metric"
    ws.Range("B1").Value = "Value"
    ws.Range("A2").Value = "Total price rows"
    ws.Range("B2").Value = totalPrices
    ws.Range("A3").Value = "Total deal rows"
    ws.Range("B3").Value = totalDeals
    ws.Range("A4").Value = "Data folder"
    ws.Range("B4").Value = dataRoot

    ApplyTableFormatting ws, 4
End Sub