Attribute VB_Name = "GroceryStoreConfig"
Option Explicit

Public Const STORE_COUNT As Long = 3

Public Sub GetStoreConfig(ByVal index As Long, ByRef storeName As String, _
    ByRef folderName As String, ByRef pricesSheet As String, ByRef dealsSheet As String)

    Select Case index
        Case 1
            storeName = "Whole Foods"
            folderName = "wholefoods"
            pricesSheet = "Whole Foods"
            dealsSheet = "Whole Foods Deals"
        Case 2
            storeName = "Food Lion"
            folderName = "foodlion"
            pricesSheet = "Food Lion"
            dealsSheet = "Food Lion Deals"
        Case 3
            storeName = "Harris Teeter"
            folderName = "harristeeter"
            pricesSheet = "Harris Teeter"
            dealsSheet = "Harris Teeter Deals"
        Case Else
            Err.Raise vbObjectError + 514, "GroceryStoreConfig", "Invalid store index: " & index
    End Select
End Sub

Public Function PricesCsvPath(ByVal dataRoot As String, ByVal folderName As String) As String
    PricesCsvPath = dataRoot & "\" & folderName & "\prices.csv"
End Function

Public Function DealsCsvPath(ByVal dataRoot As String, ByVal folderName As String) As String
    DealsCsvPath = dataRoot & "\" & folderName & "\deals.csv"
End Function