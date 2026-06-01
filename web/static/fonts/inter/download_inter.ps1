# Inter-Font Lokal-Download (DSGVO-konform)
# ============================================
# Erstellt: 2026-05-16
# Quelle: https://github.com/rsms/inter (v4.x)
#
# Laedt die 4 benoetigten WOFF2-Files in dieses Verzeichnis.

$ErrorActionPreference = "Stop"

# Base-URL: fontsource bietet CDN-Mirroring der einzelnen WOFF2-Files
$baseUrl = "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.woff2"

$fonts = @{
    "Inter-Regular.woff2"  = "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-400-normal.woff2"
    "Inter-Medium.woff2"   = "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-500-normal.woff2"
    "Inter-SemiBold.woff2" = "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-600-normal.woff2"
    "Inter-Bold.woff2"     = "https://cdn.jsdelivr.net/fontsource/fonts/inter@latest/latin-700-normal.woff2"
}

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "Lade Inter-WOFF2-Files nach $scriptDir ..." -ForegroundColor Cyan
foreach ($name in $fonts.Keys) {
    $url = $fonts[$name]
    $target = Join-Path $scriptDir $name
    Write-Host "  -> $name" -ForegroundColor Gray
    try {
        Invoke-WebRequest -Uri $url -OutFile $target -UseBasicParsing
        $size = (Get-Item $target).Length
        Write-Host "     OK ($([Math]::Round($size/1KB, 1)) KB)" -ForegroundColor Green
    } catch {
        Write-Host "     FEHLER: $_" -ForegroundColor Red
        exit 1
    }
}

Write-Host ""
Write-Host "[OK] Alle 4 WOFF2-Files gespeichert. Naechster Schritt:" -ForegroundColor Green
Write-Host "  1. Web-Dashboard restarten (systemctl restart web-dashboard)" -ForegroundColor White
Write-Host "  2. Dashboard im Browser oeffnen + DevTools Network-Tab pruefen" -ForegroundColor White
Write-Host "  3. Filter 'googleapis' sollte 0 Requests zeigen" -ForegroundColor White
