param([string]$Output = "")
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$csc = Join-Path $env:WINDIR "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
if (-not (Test-Path -LiteralPath $csc)) { throw "Der Windows-.NET-Framework-Compiler wurde nicht gefunden." }
if (-not $Output) { $Output = Join-Path $root "dist/PDF-Werkbank-1.17.0.exe" }
$Output = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force (Split-Path -Parent $Output) | Out-Null
$refs = @("System.Drawing.dll","System.Windows.Forms.dll","System.Net.Http.dll",
          "System.IO.Compression.dll","System.IO.Compression.FileSystem.dll")
$resources = @("pdf_werkbank.py","local_ai.py","online_updates.py","update_worker.py","pdf-werkbank.ico","page_tools.py","assistants.py","recovery.py","pdf_standards.py","document_review.py","filing.py","preferences.py","archive_run.py","office_export.py","intake.py","pdf_signatures.py","LICENSE","THIRD_PARTY.md","DEPENDENCIES.tsv")
$arguments = @("/nologo", "/codepage:65001", "/target:winexe", "/platform:x64", "/optimize+", "/out:$Output")
$arguments += "/win32icon:$(Join-Path $root 'pdf-werkbank.ico')"
$arguments += $refs | ForEach-Object { "/reference:$_" }
$arguments += $resources | ForEach-Object { "/resource:$(Join-Path $root $_),$_" }
$arguments += (Join-Path $root "Launcher.cs")
& $csc @arguments
if ($LASTEXITCODE) { exit $LASTEXITCODE }
Get-Item -LiteralPath $Output
