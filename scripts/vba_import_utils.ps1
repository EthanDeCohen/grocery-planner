function Read-VbaImportSource {
    param([string]$Path)
    $lines = Get-Content -Path $Path -Encoding UTF8
    $start = 0

    if ($lines.Count -gt 0 -and $lines[0] -like "VERSION *") {
        $start = 4
    }

    $code = New-Object System.Collections.Generic.List[string]
    for ($i = $start; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($line -match '^\s*Attribute\s+VB_') {
            continue
        }
        $code.Add($line)
    }

    # Trim leading blank lines so Option Explicit stays first.
    while ($code.Count -gt 0 -and [string]::IsNullOrWhiteSpace($code[0])) {
        $code.RemoveAt(0)
    }

    return ($code -join "`r`n")
}