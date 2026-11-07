param(
    [Parameter(Mandatory=$true)][string]$Version,
    [Parameter(Mandatory=$true)][string]$Executable,
    [Parameter(Mandatory=$true)][string]$WindowsZip,
    [Parameter(Mandatory=$true)][string]$ProtectedKey,
    [Parameter(Mandatory=$true)][string]$Output
)
$ErrorActionPreference = 'Stop'
if ($Version -notmatch '^(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})$') {
    throw 'Ungültige Versionsnummer.'
}
Add-Type -AssemblyName System.Security
$modulus = [regex]::Match((Get-Content (Join-Path $PSScriptRoot 'update_worker.py') -Raw),
    'PUBLIC_MODULUS = \(\s*"([^"]+)"').Groups[1].Value
$secret = [Security.Cryptography.ProtectedData]::Unprotect(
    [IO.File]::ReadAllBytes((Resolve-Path -LiteralPath $ProtectedKey).Path),
    $null, [Security.Cryptography.DataProtectionScope]::CurrentUser)
$rsa = [Security.Cryptography.RSACryptoServiceProvider]::new([Security.Cryptography.CspParameters]::new(24))
$rsa.PersistKeyInCsp = $false
try {
    $rsa.FromXmlString([Text.Encoding]::UTF8.GetString($secret))
    if ([Convert]::ToBase64String($rsa.ExportParameters($false).Modulus) -ne $modulus) {
        throw 'Der private Schlüssel gehört nicht zum eingebauten Prüfschlüssel.'
    }
    $exeHash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    $zipHash = (Get-FileHash -LiteralPath $WindowsZip -Algorithm SHA256).Hash.ToLowerInvariant()
    $size = (Get-Item -LiteralPath $WindowsZip).Length
    if ($size -le 0 -or $size -gt 20971520) { throw 'Ungültige Paketgröße.' }
    $message = @('PDF-Werkbank-Update-v1', $Version, $zipHash,
        $size.ToString([Globalization.CultureInfo]::InvariantCulture), $exeHash, '') -join [char]10
    $payload = [Text.Encoding]::ASCII.GetBytes($message)
    $signature = $rsa.SignData($payload, [Security.Cryptography.CryptoConfig]::MapNameToOID('SHA256'))
    $json = @{payload=[Convert]::ToBase64String($payload);signature=[Convert]::ToBase64String($signature);sha256=$exeHash}
    [IO.File]::WriteAllText([IO.Path]::GetFullPath($Output), ($json | ConvertTo-Json -Compress))
} finally {
    [Array]::Clear($secret, 0, $secret.Length)
    $rsa.Dispose()
}
