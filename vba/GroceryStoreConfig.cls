VERSION 1.0 CLASS
BEGIN
  MultiUse = -1  'True
END
Attribute VB_Name = "GroceryStoreConfig"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = False
Attribute VB_Exposed = False
Option Explicit

Private mStoreName As String
Private mFolderName As String
Private mPricesSheetName As String
Private mDealsSheetName As String

Public Property Get StoreName() As String
    StoreName = mStoreName
End Property

Public Property Let StoreName(ByVal value As String)
    mStoreName = value
End Property

Public Property Get FolderName() As String
    FolderName = mFolderName
End Property

Public Property Let FolderName(ByVal value As String)
    mFolderName = value
End Property

Public Property Get PricesSheetName() As String
    PricesSheetName = mPricesSheetName
End Property

Public Property Let PricesSheetName(ByVal value As String)
    mPricesSheetName = value
End Property

Public Property Get DealsSheetName() As String
    DealsSheetName = mDealsSheetName
End Property

Public Property Let DealsSheetName(ByVal value As String)
    mDealsSheetName = value
End Property

Public Function PricesCsvPath(ByVal dataRoot As String) As String
    PricesCsvPath = dataRoot & "\" & mFolderName & "\prices.csv"
End Function

Public Function DealsCsvPath(ByVal dataRoot As String) As String
    DealsCsvPath = dataRoot & "\" & mFolderName & "\deals.csv"
End Function

Public Sub Init(ByVal storeName As String, ByVal folderName As String, _
                ByVal pricesSheetName As String, ByVal dealsSheetName As String)
    mStoreName = storeName
    mFolderName = folderName
    mPricesSheetName = pricesSheetName
    mDealsSheetName = dealsSheetName
End Sub