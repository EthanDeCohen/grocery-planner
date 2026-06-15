VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "GroceryCsvImporter"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private Const DATA_FOLDER_NAME As String = "data"
Private Const PRICES_COMBINED_SHEET As String = "All Prices"
Private Const DEALS_COMBINED_SHEET As String = "All Deals"

Private mWorkbook As Workbook
Private mDataRoot As String
Private mStores As Collection

Public Sub Init(ByVal targetWorkbook As Workbook)
    Set mWorkbook = targetWorkbook
    mDataRoot = ResolveDataRoot(mWorkbook)
    Set mStores = BuildStoreConfigs()
End Sub

Public Function DataRoot() As String
    DataRoot = mDataRoot
End Function

Public Sub RefreshAll()
    Dim store As GroceryStoreConfig
    Dim pricesCombined As Worksheet
    Dim dealsCombined As Worksheet
    Dim nextPricesRow As Long
    Dim nextDealsRow As Long
    Dim importedPrices As Long
    Dim importedDeals As Long
    Dim totalPrices As Long
    Dim totalDeals As Long
    Dim summary As String

    If mDataRoot = "" Then
        Err.Raise vbObjectError + 513, "GroceryCsvImporter", _
            "Could not locate the data folder. Expected '" & DATA_FOLDER_NAME & _
            "' next to this workbook."
    End If

    Set pricesCombined = EnsureSheet(PRICES_COMBINED_SHEET)
    Set dealsCombined = EnsureSheet(DEALS_COMBINED_SHEET)

    ClearSheetData pricesCombined, 1
    ClearSheetData dealsCombined, 1

    nextPricesRow = 2
    nextDealsRow = 2

    For Each store In mStores
        importedPrices = ImportStoreCsv(store, store.PricesCsvPath(mDataRoot), _
            store.PricesSheetName, pricesCombined, nextPricesRow, store.StoreName, "price")
        importedDeals = ImportStoreCsv(store, store.DealsCsvPath(mDataRoot), _
            store.DealsSheetName, dealsCombined, nextDealsRow, store.StoreName, "deal")

        totalPrices = totalPrices + importedPrices
        totalDeals = totalDeals + importedDeals
    Next store

    ApplyTableFormatting pricesCombined, nextPricesRow - 1
    ApplyTableFormatting dealsCombined, nextDealsRow - 1
    UpdateSavingsSummary totalPrices, totalDeals

    summary = "Imported " & totalPrices & " price row(s) and " & totalDeals & _
        " deal row(s) from " & mDataRoot
    mWorkbook.Worksheets("Instructions").Range("B4").Value = Now
    mWorkbook.Worksheets("Instructions").Range("B5").Value = summary
End Sub

Private Function BuildStoreConfigs() As Collection
    Dim stores As New Collection
    Dim store As GroceryStoreConfig

    Set store = New GroceryStoreConfig
    store.Init "Whole Foods", "wholefoods", "Whole Foods", "Whole Foods Deals"
    stores.Add store

    Set store = New GroceryStoreConfig
    store.Init "Food Lion", "foodlion", "Food Lion", "Food Lion Deals"
    stores.Add store

    Set store = New GroceryStoreConfig
    store.Init "Harris Teeter", "harristeeter", "Harris Teeter", "Harris Teeter Deals"
    stores.Add store

    Set BuildStoreConfigs = stores
End Function

Private Function ResolveDataRoot(ByVal wb As Workbook) As String
    Dim workbookPath As String
    Dim candidate As String

    workbookPath = wb.Path
    If workbookPath = "" Then
        ResolveDataRoot = ""
        Exit Function
    End If

    candidate = workbookPath & "\" & DATA_FOLDER_NAME
    If Dir(candidate, vbDirectory) <> "" Then
        ResolveDataRoot = candidate
        Exit Function
    End If

    candidate = workbookPath & "\..\" & DATA_FOLDER_NAME
    If Dir(candidate, vbDirectory) <> "" Then
        ResolveDataRoot = candidate
        Exit Function
    End If

    ResolveDataRoot = ""
End Function

Private Function ImportStoreCsv(ByVal store As GroceryStoreConfig, ByVal csvPath As String, _
    ByVal sheetName As String, ByVal combinedSheet As Worksheet, ByRef nextCombinedRow As Long, _
    ByVal storeLabel As String, ByVal rowType As String) As Long

    Dim ws As Worksheet
    Dim rowCount As Long

    Set ws = EnsureSheet(sheetName)
    ClearSheetData ws, 1

    If Dir(csvPath) = "" Then
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
    Dim lastRow As Long
    Dim dataRows As Long
    ws.Range("A1").CurrentRegion.Clear
    ws.Range("A1").Value = "store"
    ws.Range("B1").Value = "row_type"

    Set queryTable = ws.QueryTables.Add( _
        Connection:="TEXT;" & csvPath, _
        Destination:=ws.Range("C1"))

    With queryTable
        .TextFileParseType = xlDelimited
        .TextFileCommaDelimiter = True
        .TextFileConsecutiveDelimiter = False
        .TextFileTrailingMinusNumbers = True
        .Refresh BackgroundQuery:=False
        .Delete
    End With

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

Private Function EnsureSheet(ByVal sheetName As String) As Worksheet
    On Error Resume Next
    Set EnsureSheet = mWorkbook.Worksheets(sheetName)
    On Error GoTo 0

    If EnsureSheet Is Nothing Then
        Set EnsureSheet = mWorkbook.Worksheets.Add(After:=mWorkbook.Worksheets(mWorkbook.Worksheets.Count))
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

Private Sub UpdateSavingsSummary(ByVal totalPrices As Long, ByVal totalDeals As Long)
    Dim ws As Worksheet
    Set ws = EnsureSheet("Savings Summary")

    ws.Range("A1").Value = "Metric"
    ws.Range("B1").Value = "Value"
    ws.Range("A2").Value = "Total price rows"
    ws.Range("B2").Value = totalPrices
    ws.Range("A3").Value = "Total deal rows"
    ws.Range("B3").Value = totalDeals
    ws.Range("A4").Value = "Data folder"
    ws.Range("B4").Value = mDataRoot

    ApplyTableFormatting ws, 4
End Sub